"""
Unit-tests for app.rate_limiter.

All tests are asynchronous and rely on aiohttp’s pytest plugin.
"""

import asyncio
import os

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from typing import AsyncGenerator

# ------------------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------------------
@pytest_asyncio.fixture(scope="session")
def event_loop():
    """Create an event loop visible to all tests (required by pytest-asyncio)."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def client(monkeypatch) -> AsyncGenerator[TestClient, None]:
    """
    Spin up the rate-limiter service with MAX_QPM=3 for quick feedback.
    """
    os.environ["MAX_QPM"] = "3"

    from app import init_app, RateLimiter

    # Make the window as short as 2 seconds to avoid 60-second sleeps
    monkeypatch.setattr(RateLimiter, "WINDOW_SECONDS", 2)

    app: web.Application = await init_app()
    server = TestServer(app)
    async with server:
        async with TestClient(server) as c:
            yield c


# ------------------------------------------------------------------------------
# Helper
# ------------------------------------------------------------------------------
async def post_json(client: TestClient, url: str, payload: dict):
    resp = await client.post(url, json=payload)
    assert resp.status == 200, await resp.text()
    return await resp.json()

async def print_status(client: TestClient):
    status = await client.get("/status")
    js = await status.json()
    print(f"Status: {js}")

# ------------------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_qpm_limit_and_queue(client: TestClient):
    """Verify that only MAX_QPM requests are admitted immediately."""
    ids = [f"req-{i}" for i in range(5)]  # 5 > MAX_QPM(=3)

    # First three requests should be admitted immediately (queue_position == 0)
    for idx in range(3):
        body = await post_json(client, "/check", {"request_id": ids[idx]})
        assert body["queue_position"] == 0

    # Fourth request must be queued (position 0 inside queue)
    body = await post_json(client, "/check", {"request_id": ids[3]})
    assert body["queue_position"] == 1  # index 1? (queued after 0)
    # Now there should be 3 active + 1 queued
    status = await client.get("/status")
    js = await status.json()
    assert js["active_requests"] == 3
    assert js["queue_length"] == 1

    # Fifth request must be queued (position 0 inside queue)
    body = await post_json(client, "/check", {"request_id": ids[4]})
    assert body["queue_position"] == 2  # index 2? (queued after 0)
    # Now there should be 3 active + 2 queued
    status = await client.get("/status")
    js = await status.json()
    assert js["active_requests"] == 3
    assert js["queue_length"] == 2

    # Fourth request already be queued (position 0 inside queue)
    body = await post_json(client, "/check", {"request_id": ids[3]})
    assert body["queue_position"] == 1  # index 1? (queued after 0)
    # Now there should be 3 active + 1 queued
    status = await client.get("/status")
    js = await status.json()
    assert js["active_requests"] == 3
    assert js["queue_length"] == 2

    # already active (position 0 inside queue)
    body = await post_json(client, "/check", {"request_id": ids[2]})
    assert body["queue_position"] == -1


@pytest.mark.asyncio
async def test_release_promotes_next(client: TestClient):
    """Releasing an active request should promote the first queued request."""
    # Fill active slots and one queued
    ids = [f"rel-{i}" for i in range(4)]
    for rid in ids:
        await post_json(client, "/check", {"request_id": rid})

    # Explicitly release the first request
    await post_json(client, "/release", {"request_id": ids[0]})

    status = await client.get("/status")
    js = await status.json()
    await print_status(client)
    # become 3 active, 0 queued
    assert js["active_requests"] == 3
    assert js["queue_length"] == 0
    # The formerly queued request should now be active
    assert ids[3] not in js["queue"]


@pytest.mark.asyncio
async def test_auto_expiration(client: TestClient):
    """Active requests should auto-expire after WINDOW_SECONDS (patched = 2)."""
    rid = "auto-expire"
    await post_json(client, "/check", {"request_id": rid})

    status = await client.get("/status")
    js = await status.json()
    assert js["active_requests"] == 1

    # Wait for 2.2 s (WINDOW_SECONDS + epsilon) so that it should expire
    await asyncio.sleep(2.2)

    status = await client.get("/status")
    js = await status.json()
    assert js["active_requests"] == 0