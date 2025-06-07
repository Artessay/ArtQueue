#!/usr/bin/env python
# -*- coding: utf-8 -*-

import asyncio
import logging
from datetime import datetime, timedelta, timezone

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

    # --------------------------------------------------------------------- #
    # Internal helpers
    # --------------------------------------------------------------------- #
    def _now(self) -> datetime:
        """Return current UTC timestamp."""
        return datetime.now(timezone.utc)

    def _purge_expired(self) -> None:
        """Remove requests whose 60-second window has expired."""
        deadline = self._now() - timedelta(seconds=self.WINDOW_SECONDS)
        expired_ids = [rid for rid, ts in self._active.items() if ts < deadline]
        for rid in expired_ids:
            logger.debug("Releasing expired request %s", rid)
            del self._active[rid]

    def _promote_waiting(self) -> None:
        """Move queued requests to active while capacity is available."""
        assert False, "This function is only useful after the notify function is implemented."
        logger.warning("This function is only useful after the notify function is implemented.")
        while self._queue and len(self._active) < self.max_qpm:
            rid = self._queue.pop(0)
            self._active[rid] = self._now()
            logger.info(
                "Request %s promoted from queue. Active=%d  Queue=%d",
                rid,
                len(self._active),
                len(self._queue),
            )


    # --------------------------------------------------------------------- #
    # Public API
    # --------------------------------------------------------------------- #
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
                # without notify, we can't move the request to the front of the queue
                if len(self._active) < self.max_qpm:
                    # dequeue and admit
                    self._queue.remove(request_id)
                    self._active[request_id] = self._now()
                    logger.info(
                        "Request %s promoted from queue. Active=%d  Queue=%d",
                        request_id,
                        len(self._active),
                        len(self._queue),
                    )
                    return 0
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
            # self._promote_waiting()
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

