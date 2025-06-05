import os
import uuid
import time
import json
import dotenv
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any
import asyncio
import aiohttp
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from concurrent.futures import ThreadPoolExecutor

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 环境变量配置
dotenv.load_dotenv()
MAX_CONCURRENCY = int(os.environ.get("MAX_CONCURRENCY", 10))
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_API_BASE = os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1")
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", 60))  # 秒
QUEUE_TIMEOUT = int(os.environ.get("QUEUE_TIMEOUT", 300))  # 秒
CACHE_ENABLED = os.environ.get("CACHE_ENABLED", "true").lower() == "true"
CACHE_TTL = int(os.environ.get("CACHE_TTL", 300))  # 秒

# 定义数据模型
class RequestData(BaseModel):
    """前端请求数据模型"""
    model: str = Field(..., description="模型名称，如gpt-3.5-turbo")
    messages: List[Dict[str, str]] = Field(..., description="消息列表")
    temperature: Optional[float] = Field(0.7, description="温度参数")
    max_tokens: Optional[int] = Field(1000, description="最大生成token数")
    stream: Optional[bool] = Field(False, description="是否流式响应")
    # 可扩展其他OpenAI API参数

class SubmitRequest(BaseModel):
    """提交请求的模型"""
    request_data: RequestData = Field(..., description="请求数据")
    callback_url: Optional[str] = Field(None, description="回调URL，处理完成后通知")

class RequestStatus(BaseModel):
    """请求状态模型"""
    request_id: str = Field(..., description="请求ID")
    status: str = Field(..., description="状态：queued, processing, completed, failed, timeout")
    queue_position: Optional[int] = Field(None, description="排队位置")
    estimated_wait_time: Optional[int] = Field(None, description="预估等待时间(秒)")
    processing_time: Optional[float] = Field(None, description="已处理时间(秒)")
    created_at: str = Field(..., description="创建时间")
    updated_at: str = Field(..., description="更新时间")
    result: Optional[Dict[str, Any]] = Field(None, description="处理结果")
    error: Optional[str] = Field(None, description="错误信息")

class MiddlewareStatus(BaseModel):
    """中间件状态模型"""
    max_concurrency: int = Field(..., description="最大并发数")
    active_requests: int = Field(..., description="当前活跃请求数")
    queue_length: int = Field(..., description="队列长度")
    total_requests: int = Field(..., description="总请求数")
    completed_requests: int = Field(..., description="已完成请求数")
    failed_requests: int = Field(..., description="失败请求数")
    average_wait_time: float = Field(..., description="平均等待时间(秒)")
    average_processing_time: float = Field(..., description="平均处理时间(秒)")
    queue: List[str] = Field(..., description="队列中的请求ID列表")

# 并发控制中间件核心类
class ConcurrencyMiddleware:
    def __init__(self, max_concurrency: int = 10):
        self.max_concurrency = max_concurrency
        self.active_requests = 0
        self.queue: List[str] = []
        self.requests: Dict[str, Dict[str, Any]] = {}
        self.stats = {
            "total_requests": 0,
            "completed_requests": 0,
            "failed_requests": 0,
            "wait_time_sum": 0,
            "processing_time_sum": 0,
            "request_history": [],
        }
        self.lock = asyncio.Lock()
        self.executor = ThreadPoolExecutor(max_workers=max_concurrency * 2)
        self.cache: Dict[str, Dict[str, Any]] = {} if CACHE_ENABLED else None
        self.session = aiohttp.ClientSession()
    
    async def close(self):
        """关闭资源"""
        await self.session.close()
        self.executor.shutdown()
    
    async def submit_request(self, request_data: RequestData, callback_url: Optional[str] = None) -> str:
        """提交请求到中间件"""
        request_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        
        # 生成缓存键（如果启用缓存）
        cache_key = None
        if self.cache is not None:
            cache_key = self._generate_cache_key(request_data)
            cached_result = self._check_cache(cache_key)
            if cached_result:
                logger.info(f"Request {request_id} found in cache")
                self.requests[request_id] = {
                    "status": "completed",
                    "created_at": now,
                    "updated_at": now,
                    "result": cached_result,
                    "processing_time": 0,
                    "wait_time": 0,
                }
                return request_id
        
        # 创建请求记录
        self.requests[request_id] = {
            "status": "queued",
            "request_data": request_data.dict(),
            "callback_url": callback_url,
            "queue_position": None,
            "estimated_wait_time": None,
            "created_at": now,
            "updated_at": now,
            "cache_key": cache_key,
        }
        
        self.stats["total_requests"] += 1
        
        # 检查是否可以立即处理
        async with self.lock:
            if self.active_requests < self.max_concurrency:
                self.active_requests += 1
                self.requests[request_id]["status"] = "processing"
                self.requests[request_id]["updated_at"] = datetime.now().isoformat()
                self.requests[request_id]["start_processing_at"] = datetime.now().timestamp()
                asyncio.create_task(self._forward_request(request_id))
                logger.info(f"Request {request_id} started processing immediately")
            else:
                position = len(self.queue)
                self.queue.append(request_id)
                self.requests[request_id]["queue_position"] = position
                self.requests[request_id]["estimated_wait_time"] = self._estimate_wait_time(position)
                logger.info(f"Request {request_id} added to queue at position {position}")
        
        # 启动队列处理任务（如果队列中有请求）
        if len(self.queue) > 0:
            asyncio.create_task(self._process_queue())
        
        # 启动超时监控
        asyncio.create_task(self._monitor_timeout(request_id))
        
        return request_id
    
    async def get_request_status(self, request_id: str) -> Dict[str, Any]:
        """获取请求状态"""
        if request_id not in self.requests:
            raise HTTPException(status_code=404, detail="Request not found")
        
        request_info = self.requests[request_id].copy()
        
        # 计算当前处理时间
        if request_info["status"] == "processing":
            start_time = request_info.get("start_processing_at", time.time())
            request_info["processing_time"] = time.time() - start_time
        
        # 计算队列位置（如果仍在排队）
        if request_info["status"] == "queued" and request_info.get("queue_position") is not None:
            try:
                current_position = self.queue.index(request_id)
                request_info["queue_position"] = current_position
                request_info["estimated_wait_time"] = self._estimate_wait_time(current_position)
            except ValueError:
                # 请求可能已被处理，但状态尚未更新
                pass
        
        return request_info
    
    async def get_middleware_status(self) -> Dict[str, Any]:
        """获取中间件整体状态"""
        async with self.lock:
            active_count = self.active_requests
            queue_length = len(self.queue)
        
        completed_count = self.stats["completed_requests"]
        failed_count = self.stats["failed_requests"]
        total_requests = self.stats["total_requests"]
        
        avg_wait_time = 0
        avg_processing_time = 0
        
        if completed_count > 0:
            avg_wait_time = self.stats["wait_time_sum"] / completed_count
            avg_processing_time = self.stats["processing_time_sum"] / completed_count
        
        return {
            "max_concurrency": self.max_concurrency,
            "active_requests": active_count,
            "queue_length": queue_length,
            "total_requests": total_requests,
            "completed_requests": completed_count,
            "failed_requests": failed_count,
            "average_wait_time": avg_wait_time,
            "average_processing_time": avg_processing_time,
            "queue": self.queue.copy(),
        }
    
    async def _process_queue(self):
        """处理队列中的请求"""
        async with self.lock:
            if self.active_requests >= self.max_concurrency or not self.queue:
                return
            
            # 检查队列中是否有未超时的请求
            for i, request_id in enumerate(self.queue):
                if request_id not in self.requests:
                    continue
                
                request_info = self.requests[request_id]
                created_time = datetime.fromisoformat(request_info["created_at"])
                elapsed = (datetime.now() - created_time).total_seconds()
                
                if elapsed > QUEUE_TIMEOUT:
                    # 请求已超时
                    logger.info(f"Request {request_id} timed out in queue after {elapsed:.2f}s")
                    self._update_request_status(request_id, "timeout", error=f"Queue timeout after {QUEUE_TIMEOUT}s")
                    continue
                
                # 找到第一个未超时的请求
                self.active_requests += 1
                self.queue.pop(i)
                
                # 更新请求状态
                self.requests[request_id]["status"] = "processing"
                self.requests[request_id]["updated_at"] = datetime.now().isoformat()
                self.requests[request_id]["start_processing_at"] = datetime.now().timestamp()
                self.requests[request_id]["queue_position"] = None
                self.requests[request_id]["estimated_wait_time"] = None
                
                # 启动请求处理
                asyncio.create_task(self._forward_request(request_id))
                logger.info(f"Request {request_id} moved from queue to processing")
                
                # 只处理一个请求，避免锁持有时间过长
                break
    
    async def _forward_request(self, request_id: str):
        """转发请求到OpenAI API"""
        request_info = self.requests[request_id]
        request_data = request_info["request_data"]
        
        logger.info(f"Forwarding request {request_id} to OpenAI API")
        
        try:
            # 构建请求参数
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {OPENAI_API_KEY}"
            }
            
            # 发送请求到OpenAI API
            start_time = time.time()
            url = f"{OPENAI_API_BASE}/chat/completions"
            
            async with self.session.post(
                url, 
                headers=headers, 
                json=request_data,
                timeout=REQUEST_TIMEOUT
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    processing_time = time.time() - start_time
                    
                    # 更新请求状态
                    self._update_request_status(
                        request_id, 
                        "completed", 
                        result=result, 
                        processing_time=processing_time
                    )
                    
                    # 如果启用缓存，存储结果
                    if self.cache is not None and request_info.get("cache_key"):
                        self._store_cache(request_info["cache_key"], result)
                    
                    logger.info(f"Request {request_id} completed in {processing_time:.2f}s")
                else:
                    error_msg = await response.text()
                    processing_time = time.time() - start_time
                    
                    self._update_request_status(
                        request_id, 
                        "failed", 
                        error=f"OpenAI API error: {response.status} - {error_msg}",
                        processing_time=processing_time
                    )
                    
                    logger.error(f"Request {request_id} failed with status {response.status}: {error_msg}")
        
        except Exception as e:
            processing_time = time.time() - start_time
            self._update_request_status(
                request_id, 
                "failed", 
                error=f"Request processing error: {str(e)}",
                processing_time=processing_time
            )
            logger.exception(f"Error processing request {request_id}")
        
        finally:
            # 释放资源并处理队列
            async with self.lock:
                self.active_requests -= 1
            
            # 处理下一个排队的请求
            asyncio.create_task(self._process_queue())
            
            # 发送回调通知（如果有）
            if request_info.get("callback_url"):
                asyncio.create_task(self._send_callback(request_id, request_info["callback_url"]))
    
    def _update_request_status(
        self, 
        request_id: str, 
        status: str, 
        result: Optional[Dict[str, Any]] = None, 
        error: Optional[str] = None,
        processing_time: Optional[float] = None
    ):
        """更新请求状态"""
        if request_id not in self.requests:
            return
        
        now = datetime.now().isoformat()
        request_info = self.requests[request_id]
        
        # 计算等待时间
        created_time = datetime.fromisoformat(request_info["created_at"])
        wait_time = (datetime.now() - created_time).total_seconds()
        
        # 更新请求信息
        self.requests[request_id].update({
            "status": status,
            "updated_at": now,
            "result": result,
            "error": error,
            "wait_time": wait_time,
            "processing_time": processing_time
        })
        
        # 更新统计信息
        if status == "completed":
            self.stats["completed_requests"] += 1
            self.stats["wait_time_sum"] += wait_time
            if processing_time:
                self.stats["processing_time_sum"] += processing_time
            
            # 记录历史
            self.stats["request_history"].append({
                "request_id": request_id,
                "created_at": request_info["created_at"],
                "completed_at": now,
                "wait_time": wait_time,
                "processing_time": processing_time,
                "model": request_info["request_data"].get("model", "unknown")
            })
            
            # 保持历史记录在合理范围内
            if len(self.stats["request_history"]) > 1000:
                self.stats["request_history"] = self.stats["request_history"][-1000:]
                
        elif status == "failed" or status == "timeout":
            self.stats["failed_requests"] += 1
    
    async def _monitor_timeout(self, request_id: str):
        """监控请求超时"""
        await asyncio.sleep(QUEUE_TIMEOUT)
        
        if request_id not in self.requests:
            return
        
        request_info = self.requests[request_id]
        if request_info["status"] in ["queued", "processing"]:
            # 计算实际等待时间
            created_time = datetime.fromisoformat(request_info["created_at"])
            actual_wait_time = (datetime.now() - created_time).total_seconds()
            
            # 标记为超时
            self._update_request_status(
                request_id, 
                "timeout", 
                error=f"Request timed out after {actual_wait_time:.2f}s"
            )
            
            logger.warning(f"Request {request_id} timed out after {actual_wait_time:.2f}s")
            
            # 如果在处理中，需要释放资源
            if request_info["status"] == "processing":
                async with self.lock:
                    self.active_requests -= 1
                
                # 处理下一个排队的请求
                asyncio.create_task(self._process_queue())
    
    async def _send_callback(self, request_id: str, callback_url: str):
        """发送回调通知"""
        if request_id not in self.requests:
            return
        
        request_info = self.requests[request_id]
        
        # 构建回调数据
        callback_data = {
            "request_id": request_id,
            "status": request_info["status"],
            "created_at": request_info["created_at"],
            "updated_at": request_info["updated_at"],
            "wait_time": request_info.get("wait_time"),
            "processing_time": request_info.get("processing_time"),
            "result": request_info.get("result"),
            "error": request_info.get("error")
        }
        
        try:
            async with self.session.post(
                callback_url,
                json=callback_data,
                timeout=5  # 回调超时时间
            ) as response:
                if response.status >= 400:
                    logger.warning(f"Callback failed with status {response.status}: {callback_url}")
                else:
                    logger.info(f"Callback sent successfully: {callback_url}")
        except Exception as e:
            logger.error(f"Error sending callback to {callback_url}: {str(e)}")
    
    def _estimate_wait_time(self, position: int) -> int:
        """估计等待时间（秒）"""
        # 简单估算：每个位置约等待10秒，加上当前活跃请求处理时间
        if self.stats["completed_requests"] > 0:
            avg_time = self.stats["processing_time_sum"] / self.stats["completed_requests"]
            return max(10, int(position * avg_time / self.max_concurrency))
        else:
            return position * 10
    
    def _generate_cache_key(self, request_data: RequestData) -> str:
        """生成缓存键"""
        # 基于模型、消息内容和参数生成缓存键
        messages_hash = hash(tuple(json.dumps(msg, sort_keys=True) for msg in request_data.messages))
        return f"{request_data.model}:{messages_hash}:{request_data.temperature}:{request_data.max_tokens}"
    
    def _check_cache(self, cache_key: str) -> Optional[Dict[str, Any]]:
        """检查缓存"""
        if not self.cache:
            return None
        
        if cache_key in self.cache:
            cache_entry = self.cache[cache_key]
            if time.time() - cache_entry["timestamp"] < CACHE_TTL:
                return cache_entry["result"]
        
        return None
    
    def _store_cache(self, cache_key: str, result: Dict[str, Any]):
        """存储缓存"""
        if not self.cache:
            return
        
        self.cache[cache_key] = {
            "timestamp": time.time(),
            "result": result
        }

# 创建FastAPI应用
app = FastAPI(
    title="OpenAI API Concurrency Middleware",
    description="A middleware for controlling concurrency and queueing requests to OpenAI API",
    version="1.0.0"
)

# 初始化中间件
middleware = ConcurrencyMiddleware(max_concurrency=MAX_CONCURRENCY)

# 添加关闭事件处理
@app.on_event("shutdown")
async def shutdown_event():
    await middleware.close()

# API路由
@app.post("/submit", response_model=Dict[str, str], status_code=202)
async def submit_request(
    submit_data: SubmitRequest,
    background_tasks: BackgroundTasks
):
    """
    提交请求到中间件处理
    
    返回:
    - request_id: 请求ID
    - status_url: 查询状态的URL
    """
    try:
        request_id = await middleware.submit_request(
            request_data=submit_data.request_data,
            callback_url=submit_data.callback_url
        )
        
        return {
            "request_id": request_id,
            "status_url": f"/query/{request_id}",
            "message": "Request submitted successfully. Check status URL for progress."
        }
    except Exception as e:
        logger.error(f"Error submitting request: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@app.get("/query/{request_id}", response_model=RequestStatus)
async def query_request_status(request_id: str):
    """查询请求状态"""
    try:
        status = await middleware.get_request_status(request_id)
        return status
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error querying request status: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@app.get("/status", response_model=MiddlewareStatus)
async def get_middleware_status():
    """获取中间件整体状态"""
    try:
        status = await middleware.get_middleware_status()
        return status
    except Exception as e:
        logger.error(f"Error getting middleware status: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@app.delete("/cancel/{request_id}", response_model=Dict[str, str])
async def cancel_request(request_id: str):
    """取消请求"""
    try:
        if request_id not in middleware.requests:
            raise HTTPException(status_code=404, detail="Request not found")
        
        request_info = middleware.requests[request_id]
        if request_info["status"] in ["completed", "failed", "timeout"]:
            raise HTTPException(status_code=400, detail="Cannot cancel already completed request")
        
        # 更新状态为取消
        middleware._update_request_status(
            request_id, 
            "cancelled", 
            error="Request cancelled by user"
        )
        
        # 如果在处理中，需要释放资源
        if request_info["status"] == "processing":
            async with middleware.lock:
                middleware.active_requests -= 1
            
            # 处理下一个排队的请求
            asyncio.create_task(middleware._process_queue())
        
        # 从队列中移除（如果存在）
        async with middleware.lock:
            if request_id in middleware.queue:
                middleware.queue.remove(request_id)
        
        return {"status": "success", "message": "Request cancelled successfully"}
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error cancelling request: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

# 支持流式响应的端点
@app.post("/submit-stream")
async def submit_stream_request(
    submit_data: SubmitRequest,
    background_tasks: BackgroundTasks
):
    """
    提交流式请求到中间件处理
    
    注意：流式请求不支持缓存和回调
    """
    try:
        # 禁用流式响应的缓存
        original_stream = submit_data.request_data.stream
        submit_data.request_data.stream = False
        
        request_id = await middleware.submit_request(
            request_data=submit_data.request_data,
            callback_url=None  # 流式请求不支持回调
        )
        
        # 恢复原始设置
        submit_data.request_data.stream = original_stream
        
        # 创建一个特殊的状态URL，用于流式响应
        return {
            "request_id": request_id,
            "status_url": f"/query/{request_id}",
            "stream_url": f"/stream/{request_id}",
            "message": "Stream request submitted successfully. Use stream_url to get real-time response."
        }
    except Exception as e:
        logger.error(f"Error submitting stream request: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

# 流式响应端点
@app.get("/stream/{request_id}")
async def stream_response(request_id: str, request: Request):
    """获取流式响应"""
    try:
        if request_id not in middleware.requests:
            raise HTTPException(status_code=404, detail="Request not found")
        
        request_info = middleware.requests[request_id]
        
        # 检查请求是否已完成
        if request_info["status"] != "completed":
            if request_info["status"] == "processing":
                return JSONResponse(
                    content={"status": "processing", "message": "Request is still being processed"},
                    status_code=202
                )
            else:
                raise HTTPException(
                    status_code=400, 
                    detail=f"Cannot stream response for status: {request_info['status']}"
                )
        
        # 获取结果
        result = request_info.get("result")
        if not result:
            raise HTTPException(status_code=500, detail="No result available")
        
        # 模拟流式响应
        async def stream_generator():
            # 提取内容
            content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
            
            # 按字符分割并发送
            for char in content:
                yield f"data: {json.dumps({'content': char})}\n\n"
                await asyncio.sleep(0.01)  # 模拟打字速度
            
            # 发送结束标记
            yield "data: [DONE]\n\n"
        
        return Response(
            stream_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"}
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error streaming response: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

# 允许跨域请求
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 实际生产环境中应限制为特定域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 启动应用
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
