import asyncio
import requests
import json
import argparse
from rich.console import Console
from rich.live import Live
from rich.progress import Progress
from rich.table import Table

console = Console()

class OpenAIMiddlewareClient:
    def __init__(self, middleware_url="http://localhost:8080"):
        self.middleware_url = middleware_url
        self.session = requests.Session()
    
    def submit_request(self, request_data, callback_url=None):
        """提交请求到中间件"""
        endpoint = f"{self.middleware_url}/submit"
        payload = {
            "request_data": request_data,
            "callback_url": callback_url
        }
        
        try:
            response = self.session.post(endpoint, json=payload)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            console.print(f"[red]Error submitting request: {str(e)}[/red]")
            return None
    
    def submit_stream_request(self, request_data):
        """提交流式请求到中间件"""
        endpoint = f"{self.middleware_url}/submit-stream"
        payload = {
            "request_data": request_data,
            "callback_url": None  # 流式请求不支持回调
        }
        
        try:
            response = self.session.post(endpoint, json=payload)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            console.print(f"[red]Error submitting stream request: {str(e)}[/red]")
            return None
    
    def get_request_status(self, request_id):
        """查询请求状态"""
        endpoint = f"{self.middleware_url}/query/{request_id}"
        
        try:
            response = self.session.get(endpoint)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            console.print(f"[red]Error getting request status: {str(e)}[/red]")
            return None
    
    def get_middleware_status(self):
        """获取中间件整体状态"""
        endpoint = f"{self.middleware_url}/status"
        
        try:
            response = self.session.get(endpoint)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            console.print(f"[red]Error getting middleware status: {str(e)}[/red]")
            return None
    
    def cancel_request(self, request_id):
        """取消请求"""
        endpoint = f"{self.middleware_url}/cancel/{request_id}"
        
        try:
            response = self.session.delete(endpoint)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            console.print(f"[red]Error cancelling request: {str(e)}[/red]")
            return None
    
    def stream_response(self, request_id):
        """获取流式响应"""
        endpoint = f"{self.middleware_url}/stream/{request_id}"
        
        try:
            response = self.session.get(endpoint, stream=True)
            response.raise_for_status()
            
            full_response = ""
            for line in response.iter_lines():
                if line:
                    line = line.decode('utf-8')
                    if line.startswith('data: '):
                        data = json.loads(line[6:])
                        if data == "[DONE]":
                            break
                        content = data.get("content", "")
                        full_response += content
                        yield content
            
            return full_response
        except requests.RequestException as e:
            console.print(f"[red]Error streaming response: {str(e)}[/red]")
            return None

# 示例使用
async def submit_and_wait_async(request_data, client, show_progress=True):
    """异步提交请求并等待结果"""
    # 提交请求
    result = client.submit_request(request_data)
    if not result:
        return None
    
    request_id = result["request_id"]
    status_url = result["status_url"]
    
    console.print(f"[green]Request submitted successfully. ID: {request_id}[/green]")
    console.print(f"Status URL: {status_url}")
    
    # 显示进度
    if show_progress:
        progress = Progress()
        task = progress.add_task("[cyan]Waiting for response...", total=None)
        
        with Live(progress, refresh_per_second=4):
            while True:
                status = client.get_request_status(request_id)
                if not status:
                    progress.update(task, description="[red]Error getting status[/red]")
                    break
                
                current_status = status["status"]
                
                if current_status == "queued":
                    position = status.get("queue_position", "unknown")
                    estimated_wait = status.get("estimated_wait_time", "unknown")
                    progress.update(
                        task, 
                        description=f"[yellow]Queued at position {position} (estimated wait: {estimated_wait}s)[/yellow]"
                    )
                elif current_status == "processing":
                    processing_time = status.get("processing_time", 0)
                    progress.update(
                        task, 
                        description=f"[blue]Processing... {processing_time:.1f}s elapsed[/blue]"
                    )
                elif current_status == "completed":
                    progress.update(task, description="[green]Completed![/green]", completed=1)
                    break
                elif current_status in ["failed", "timeout", "cancelled"]:
                    error = status.get("error", "Unknown error")
                    progress.update(task, description=f"[red]{current_status}: {error}[/red]", completed=1)
                    break
                
                await asyncio.sleep(1)  # 避免请求过于频繁
    
    # 获取最终结果
    status = client.get_request_status(request_id)
    if not status:
        return None
    
    return status

async def submit_stream_and_wait_async(request_data, client):
    """异步提交流式请求并实时显示结果"""
    # 提交请求
    result = client.submit_stream_request(request_data)
    if not result:
        return None
    
    request_id = result["request_id"]
    stream_url = result["stream_url"]
    
    console.print(f"[green]Stream request submitted successfully. ID: {request_id}[/green]")
    console.print(f"Stream URL: {stream_url}")
    
    # 显示初始进度
    progress = Progress()
    task = progress.add_task("[cyan]Waiting for response...", total=None)
    
    with Live(progress, refresh_per_second=4):
        # 等待请求进入处理状态
        while True:
            status = client.get_request_status(request_id)
            if not status:
                progress.update(task, description="[red]Error getting status[/red]")
                break
            
            current_status = status["status"]
            
            if current_status == "queued":
                position = status.get("queue_position", "unknown")
                estimated_wait = status.get("estimated_wait_time", "unknown")
                progress.update(
                    task, 
                    description=f"[yellow]Queued at position {position} (estimated wait: {estimated_wait}s)[/yellow]"
                )
            elif current_status == "processing":
                progress.update(task, description="[blue]Streaming response...[/blue]")
                break
            elif current_status in ["failed", "timeout", "cancelled"]:
                error = status.get("error", "Unknown error")
                progress.update(task, description=f"[red]{current_status}: {error}[/red]", completed=1)
                return status
            
            await asyncio.sleep(1)  # 避免请求过于频繁
    
    # 开始流式接收结果
    console.print("\n[blue]Response:[/blue]\n")
    
    full_response = ""
    for chunk in client.stream_response(request_id):
        if chunk:
            full_response += chunk
            console.print(chunk, end="")
    
    console.print("\n\n[green]Stream completed![/green]")
    
    # 获取最终状态
    status = client.get_request_status(request_id)
    return status

async def main():
    parser = argparse.ArgumentParser(description="OpenAI Middleware Client")
    parser.add_argument("--url", default="http://localhost:8080", help="Middleware URL")
    parser.add_argument("--model", default="gpt-3.5-turbo", help="Model to use")
    parser.add_argument("--temperature", type=float, default=0.7, help="Temperature parameter")
    parser.add_argument("--max-tokens", type=int, default=1000, help="Maximum tokens")
    parser.add_argument("--stream", action="store_true", help="Use streaming response")
    parser.add_argument("--query", help="Query to send to the model")
    parser.add_argument("--status", help="Check status of a request by ID")
    parser.add_argument("--cancel", help="Cancel a request by ID")
    parser.add_argument("--middleware-status", action="store_true", help="Get middleware status")
    parser.add_argument("--concurrency", type=int, default=1, help="Number of concurrent requests")
    
    args = parser.parse_args()
    
    client = OpenAIMiddlewareClient(args.url)
    
    if args.status:
        status = client.get_request_status(args.status)
        if status:
            console.print_json(json.dumps(status, indent=2))
        return
    
    if args.cancel:
        result = client.cancel_request(args.cancel)
        if result:
            console.print_json(json.dumps(result, indent=2))
        return
    
    if args.middleware_status:
        status = client.get_middleware_status()
        if status:
            console.print_json(json.dumps(status, indent=2))
        return
    
    # 如果没有指定查询内容，使用默认内容
    query = args.query or "Explain the concept of concurrency control in 200 words."
    
    # 构建请求数据
    request_data = {
        "model": args.model,
        "messages": [{"role": "user", "content": query}],
        "temperature": args.temperature,
        "max_tokens": args.max_tokens,
        "stream": args.stream
    }
    
    # 处理并发请求
    if args.concurrency > 1:
        tasks = []
        for i in range(args.concurrency):
            # 为每个请求添加唯一标识
            modified_query = f"Request {i+1}: {query}"
            modified_data = request_data.copy()
            modified_data["messages"] = [{"role": "user", "content": modified_query}]
            
            if args.stream:
                task = asyncio.create_task(submit_stream_and_wait_async(modified_data, client))
            else:
                task = asyncio.create_task(submit_and_wait_async(modified_data, client))
            
            tasks.append(task)
        
        results = await asyncio.gather(*tasks)
        
        # 显示汇总结果
        console.print("\n[bold green]Results Summary:[/bold green]")
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Request ID")
        table.add_column("Status")
        table.add_column("Processing Time (s)")
        table.add_column("Error")
        
        for result in results:
            if result:
                table.add_row(
                    result["request_id"],
                    result["status"],
                    str(result.get("processing_time", "N/A")),
                    result.get("error", "")
                )
        
        console.print(table)
    else:
        # 单个请求
        if args.stream:
            result = await submit_stream_and_wait_async(request_data, client)
        else:
            result = await submit_and_wait_async(request_data, client)
        
        if result and result["status"] == "completed":
            console.print("\n[bold green]Final Response:[/bold green]")
            content = result.get("result", {}).get("choices", [{}])[0].get("message", {}).get("content", "")
            console.print(content)

if __name__ == "__main__":
    asyncio.run(main())
