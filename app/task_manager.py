import asyncio
from typing import List, Optional, Type, Any, Coroutine

class TaskManager:
    """
    Context manager for managing asynchronous tasks.
    
    Automatically cancels and awaits all created tasks when exiting the context.
    """
    
    def __init__(self) -> None:
        """Initialize the task manager with an empty list of tasks."""
        self._tasks: List[asyncio.Task[Any]] = []
    
    async def __aenter__(self) -> 'TaskManager':
        """Enter the asynchronous context and return the task manager instance."""
        return self
    
    async def __aexit__(
        self, 
        exc_type: Optional[Type[BaseException]], 
        exc_val: Optional[BaseException], 
        exc_tb: Optional[Any]
    ) -> None:
        """
        Exit the asynchronous context and cleanup all tasks.
        
        Cancels all pending tasks and waits for them to complete.
        Exceptions from cancelled tasks are handled gracefully.
        """
        # Cancel all pending tasks
        for task in self._tasks:
            if not task.done():
                task.cancel()
        
        # Wait for all tasks to complete (either naturally or by cancellation)
        await asyncio.gather(*self._tasks, return_exceptions=True)
    
    def create_task(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        """
        Create and track a new asynchronous task.
        
        Args:
            coro: The coroutine to run as a task.
            
        Returns:
            The created asyncio.Task object.
        """
        task = asyncio.create_task(coro)
        self._tasks.append(task)
        return task
