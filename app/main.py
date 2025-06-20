import os
import asyncio
from aiohttp import web
from aiohttp_swagger3 import SwaggerDocs, SwaggerInfo, SwaggerUiSettings
from typing import Final

from app.rate_limiter import RateLimiter

RATE_LIMITER_KEY: Final[web.AppKey["RateLimiter"]] = web.AppKey("rate_limiter")

###############################################################################
# Environment configuration
###############################################################################
# Maximum queries per minute, configurable at deploy time.
MAX_QPM = int(os.getenv("MAX_QPM", "10"))

###############################################################################
# Web application & API routes
###############################################################################
async def init_app() -> web.Application:
    app = web.Application()

    app[RATE_LIMITER_KEY] = RateLimiter(MAX_QPM)

    async def on_shutdown(app: web.Application) -> None:
        """Cleanup resources on application shutdown."""
        rate_limiter: RateLimiter = app[RATE_LIMITER_KEY]
        await rate_limiter.shutdown()
    app.on_shutdown.append(on_shutdown)

    # ---------------------------------------------------- #
    # Swagger/OpenAPI setup
    # ---------------------------------------------------- #
    swagger = SwaggerDocs(
        app,
        swagger_ui_settings=SwaggerUiSettings(path="/docs/"),
        info=SwaggerInfo(
            title="Rate-Limit Service",
            version="1.0.0",
            description=(
                "Rate-limiting service that enforces a global "
                f"QPM (queries-per-minute) limit. Current MAX_QPM={MAX_QPM}"
            ),
        ),
    )

    # ---------------------------------------------------- #
    # Route handlers
    # ---------------------------------------------------- #
    async def register_handler(request: web.Request) -> web.Response:
        """
        ---
        description: |
            Register a new request with the rate-limiter.
            Server will push {"type": "queue", "data": <position>} on every state change.
        tags:
          - RateLimit
        responses:
          "200":
            description: Request registered
            content:
              application/json:
                schema:
                  type: object
                  properties:
                    request_id:
                      type: string
          "429":
            description: Too many requests
        """
        request_id = request.query.get("request_id")
        if not request_id:
            return web.json_response({"error": "Missing request_id"}, status=400)
        
        rate_limiter: RateLimiter = request.app[RATE_LIMITER_KEY]

        queue: asyncio.Queue[str] = rate_limiter.register(request_id)
        
        # prepare SSE response
        response = web.StreamResponse(
            status=200,
            reason="OK",
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )
        await response.prepare(request)

        try:
            while True:
                payload = await queue.get()
                await response.write(f"data: {payload}\n\n".encode("utf-8"))
                await response.write(b'')
        except (asyncio.CancelledError, ConnectionResetError):
            pass
        finally:
            await response.write_eof()
            rate_limiter.unregister(request_id)

        return response

    async def check_queue_handler(request: web.Request) -> web.Response:
        """
        ---
        description: |
            Try to obtain a QPM slot for the supplied `request_id`.  
            On success `queue_position == 0`, otherwise the 0-based position
            in the waiting queue is returned.
        tags:
          - RateLimit
        requestBody:
          required: true
          content:
            application/json:
              schema:
                type: object
                required: [request_id]
                properties:
                  request_id:
                    type: string
        responses:
          "200":
            description: Queue position
            content:
              application/json:
                schema:
                  type: object
                  properties:
                    queue_position:
                      type: integer
          "400":
            description: Missing request_id
        """
        data = await request.json()
        request_id = data.get("request_id")
        if not request_id:
            return web.json_response({"error": "Missing request_id"}, status=400)

        rate_limiter: RateLimiter = request.app[RATE_LIMITER_KEY]
        position = await rate_limiter.check_queue(request_id)
        return web.json_response({"queue_position": position})

    async def release_handler(request: web.Request) -> web.Response:
        """
        ---
        description: Release the QPM slot held by `request_id` manually.
        tags:
          - RateLimit
        requestBody:
          required: true
          content:
            application/json:
              schema:
                type: object
                required: [request_id]
                properties:
                  request_id:
                    type: string
        responses:
          "200":
            description: Slot released
            content:
              application/json:
                schema:
                  type: object
                  properties:
                    success:
                      type: boolean
          "400":
            description: Request not found
        """
        data = await request.json()
        request_id = data.get("request_id")
        if not request_id:
            return web.json_response({"error": "Missing request_id"}, status=400)

        rate_limiter: RateLimiter = request.app[RATE_LIMITER_KEY]
        success = await rate_limiter.release_resource(request_id)
        if not success:
            return web.json_response(
                {"error": "Request not found or already released"}, status=400
            )
        return web.json_response({"success": True})

    async def status_handler(request: web.Request) -> web.Response:
        """
        ---
        description: Return current rate-limiter status.
        tags:
          - RateLimit
        responses:
          "200":
            description: Rate Limiter status
            content:
              application/json:
                schema:
                  type: object
        """
        rate_limiter: RateLimiter = request.app[RATE_LIMITER_KEY]
        status = await rate_limiter.get_status()
        return web.json_response(status)

    # ---------------------------------------------------- #
    # Route registration
    # ---------------------------------------------------- #
    swagger.add_routes(
        [
            web.get("/register", register_handler),
            web.post("/check", check_queue_handler),
            web.post("/release", release_handler),
            web.get("/status", status_handler),
        ]
    )

    return app


###############################################################################
# Entrypoint
###############################################################################
if __name__ == "__main__":
    port = int(os.getenv("API_PORT", "8080")) 
    web.run_app(init_app(), port=port)