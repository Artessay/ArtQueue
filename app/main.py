import os
from aiohttp import web
from aiohttp_swagger3 import SwaggerDocs, SwaggerInfo

from app.rate_limiter import RateLimiter


###############################################################################
# Environment configuration
###############################################################################
# Maximum queries per minute, configurable at deploy time.
MAX_QPM = int(os.getenv("MAX_QPM", "100"))

###############################################################################
# Web application & API routes
###############################################################################
async def init_app() -> web.Application:
    app = web.Application()

    rate_limiter = RateLimiter(max_qpm=MAX_QPM)

    # ---------------------------------------------------- #
    # Swagger/OpenAPI setup
    # ---------------------------------------------------- #
    info = SwaggerInfo(
        title="Rate-Limit Service",
        version="1.0.0",
        description=(
            "Simple rate-limiting service that enforces a global "
            f"QPM (queries-per-minute) limit. MAX_QPM={MAX_QPM}"
        ),
    )
    SwaggerDocs(app, swagger_info=info)

    # ---------------------------------------------------- #
    # Route handlers
    # ---------------------------------------------------- #
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

        success = await rate_limiter.release_resource(request_id)
        if not success:
            return web.json_response(
                {"error": "Request not found or already released"}, status=400
            )
        return web.json_response({"success": True})

    async def status_handler(_: web.Request) -> web.Response:
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
        status = await rate_limiter.get_status()
        return web.json_response(status)

    # ---------------------------------------------------- #
    # Route registration
    # ---------------------------------------------------- #
    app.router.add_post("/check", check_queue_handler)
    app.router.add_post("/release", release_handler)
    app.router.add_get("/status", status_handler)

    return app


###############################################################################
# Entrypoint
###############################################################################
if __name__ == "__main__":
    web.run_app(init_app(), port=8080)