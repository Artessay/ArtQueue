import requests
import uuid
import time

class FrontendClient:
    def __init__(self, middleware_url="http://localhost:8080"):
        self.middleware_url = middleware_url
    
    def make_request(self):
        # 生成唯一请求ID
        request_id = str(uuid.uuid4())
        
        # 检查是否需要排队
        queue_position = self.check_queue(request_id)
        
        # 可以实现轮询检查队列位置，或者使用WebSocket接收通知
        while queue_position > 0:
            print(f"Request {request_id} is queued at position {queue_position}")
            time.sleep(10)  # 避免过于频繁的请求
            queue_position = self.check_queue(request_id)
        
        print(f"Request {request_id} is being processed")
        
    
    def check_queue(self, request_id):
        response = requests.post(
            f"{self.middleware_url}/check",
            json={"request_id": request_id}
        )
        if response.status_code == 200:
            return response.json()["queue_position"]
        else:
            print(f"Error checking queue: {response.text}")
            return 0  # 假设不需要排队，避免阻塞
    
    def release_resource(self, request_id):
        response = requests.post(
            f"{self.middleware_url}/release",
            json={"request_id": request_id}
        )
        if response.status_code != 200:
            print(f"Error releasing resource: {response.text}")

# 使用示例
if __name__ == "__main__":
    client = FrontendClient()
    
    # 模拟多个并发请求
    import threading
    
    def make_request():
        client.make_request()
    
    # 模拟15个并发请求，超过中间件的默认并发限制10
    threads = []
    for _ in range(15):
        t = threading.Thread(target=make_request)
        threads.append(t)
        t.start()
    
    for t in threads:
        t.join()
