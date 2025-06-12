#!/usr/bin/env python
# -*- coding: utf-8 -*-

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.event_notifier import EventNotifier

###############################################################################
# Logger
###############################################################################
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

###############################################################################
# Rate Limiter
###############################################################################
class RateLimiter:
    """
    Implements a sliding-window QPM (queries-per-minute) rate-limiter.
    A request occupies one slot for 60 seconds or until it is released
    explicitly through /release.
    """

    WINDOW_SECONDS = 60

    def __init__(self, max_qpm: int) -> None:
        # Configured limit
        self.max_qpm: int = max_qpm

        # request_id -> datetime(UTC) of admission
        self._active: dict[str, datetime] = {}

        # FIFO queue of request_ids waiting for a slot
        self._queue: list[str] = []

        # Protects all mutable structures
        self._lock = asyncio.Lock()

        # Event notifier
        self._notifier = EventNotifier()

        # Start background task to clean up expired requests
        asyncio.create_task(self._janitor())

    # --------------------------------------------------------------------- #
    # Internal helpers
    # --------------------------------------------------------------------- #
    def _now(self) -> datetime:
        """Return current UTC timestamp."""
        return datetime.now(timezone.utc)
    
    async def _janitor(self):
        while True:
            await asyncio.sleep(1)
            async with self._lock:
                self._purge_expired()

    def _purge_expired(self) -> None:
        """Remove requests whose 60-second window has expired."""
        deadline = self._now() - timedelta(seconds=self.WINDOW_SECONDS)
        expired_ids = [rid for rid, ts in self._active.items() if ts < deadline]
        for rid in expired_ids:
            logger.debug("Releasing expired request %s", rid)
            del self._active[rid]
            self.unregister(rid)

        # Promote queued requests if possible
        self._promote_waiting()

    def _promote_waiting(self) -> None:
        """Move queued requests to active while capacity is available."""
        while self._queue and len(self._active) < self.max_qpm:
            rid = self._queue.pop(0)
            self._active_request(rid)

        # Notify listeners about queue position
        for idx, rid in enumerate(self._queue):
            asyncio.create_task(
                self._notifier.notify_position(rid, idx + 1)
            )

    def _active_request(self, request_id: str) -> None:
        assert len(self._active) < self.max_qpm
        self._active[request_id] = self._now()
        
        logger.info(
            "Request %s admitted. Active=%d/%d  Queue=%d",
            request_id,
            len(self._active),
            self.max_qpm,
            len(self._queue),
        )
        asyncio.create_task(
            self._notifier.notify_position(request_id, 0)
        )

    async def _check_and_notify(self, request_id: str) -> None:
        """Notify listeners about queue position if changed."""
        position = await self.check_queue(request_id)
        await self._notifier.notify_position(request_id, position)

    # --------------------------------------------------------------------- #
    # Public API
    # --------------------------------------------------------------------- #
    def register(self, request_id: str) -> asyncio.Queue:
        """Register a new listener for `request_id`."""
        queue = self._notifier.register_listener(request_id)
        asyncio.create_task(self._check_and_notify(request_id))
        return queue
    
    def unregister(self, request_id: str) -> None:
        """Remove the listener for `request_id`."""
        self._notifier.unregister_listener(request_id)

    async def check_queue(self, request_id: str) -> int:
        """
        Try to acquire a QPM slot for `request_id`.
        Returns the queue index (0 means admitted immediately, -1 means already active).
        """
        async with self._lock:
            self._purge_expired()

            # Already active → position 0
            if request_id in self._active:
                logger.info("Request %s already active", request_id)
                return -1

            # Already in queue → return its position
            if request_id in self._queue:
                assert len(self._active) == self.max_qpm
                idx = self._queue.index(request_id) + 1 # use 1-based index
                logger.info("Request %s already in queue at pos %d", request_id, idx)
                return idx

            # Capacity still available → admit immediately
            if len(self._active) < self.max_qpm:
                self._active[request_id] = self._now()
                logger.info(
                    "Request %s admitted. Active=%d/%d",
                    request_id,
                    len(self._active),
                    self.max_qpm,
                )
                return 0

            # Otherwise enqueue
            self._queue.append(request_id)
            position = len(self._queue)
            logger.info(
                "Request %s queued at position %d. Queue length: %d",
                request_id,
                position,
                len(self._queue),
            )
            return position

    async def release_resource(self, request_id: str) -> bool:
        """
        Explicitly release a slot occupied by `request_id`.
        Returns True on success, False if not found.
        """
        async with self._lock:
            removed = self._active.pop(request_id, None)
            if not removed:
                return False

            logger.info(
                "Request %s released by client. Active=%d",
                request_id,
                len(self._active),
            )
            # Promote queued requests if possible
            self._promote_waiting()
            return True

    async def get_status(self) -> dict:
        """Return a snapshot of the current middleware state."""
        async with self._lock:
            self._purge_expired()

            return {
                "max_qpm": self.max_qpm,
                "active_requests": len(self._active),
                "queue_length": len(self._queue),
                "queue": list(self._queue),
                "stats": {
                    "active": len(self._active),
                    "queued": len(self._queue),
                },
            }

