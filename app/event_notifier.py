import json
import asyncio

class EventNotifier:
    def __init__(self):
        # listeners: request_id -> asyncio.Queue (used by SSE connections)
        self._listeners: dict[str, asyncio.Queue] = {}

    def register_listener(self, request_id: str) -> asyncio.Queue:
        """Register a new listener for `request_id`."""
        queue = asyncio.Queue()
        self._listeners[request_id] = queue
        return queue
    
    def unregister_listener(self, request_id: str) -> None:
        """Remove the listener for `request_id`."""
        self._listeners.pop(request_id, None)
        
    async def notify(self, request_id: str, payload: str) -> None:
        """Send an event to all listeners of `request_id`."""
        if isinstance(payload, dict):
            payload = json.dumps(payload)
        assert isinstance(payload, str)

        if request_id in self._listeners:
            await self._listeners[request_id].put(payload)

    async def notify_position(self, request_id: str, position: int) -> None:
        """Send a notification to the listener for `request_id`."""
        await self.notify(request_id, {"type": "queue", "data": position})