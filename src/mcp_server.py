from fastmcp import FastMCP
from fastmcp.server.middleware.logging import StructuredLoggingMiddleware
from fastmcp.server.middleware.rate_limiting import SlidingWindowRateLimitingMiddleware
from fastmcp.server.middleware.error_handling import ErrorHandlingMiddleware
import logging
from tools import *
from telemetry import telemetry
import sys
import os
import json
from datetime import datetime, timezone
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Scope, Receive, Send

MCP_AUTH_TOKEN = os.getenv("MCP_AUTH_TOKEN")

class BearerAuthMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] == "http":
            path = scope.get("path", "")
            if path not in ("/health", "/metrics"):
                headers = dict(scope.get("headers", []))
                auth = headers.get(b"authorization", b"").decode()
                if not auth.startswith("Bearer "):
                    await self._reject(send, 401, "Unauthorized")
                    return
                if auth[7:].strip() != MCP_AUTH_TOKEN:
                    await self._reject(send, 403, "Forbidden")
                    return
        await self.app(scope, receive, send)

    async def _reject(self, send, status: int, message: str):
        body = json.dumps({"error": message}).encode()
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [
                [b"content-type", b"application/json"],
                [b"content-length", str(len(body)).encode()]
            ]
        })
        await send({
            "type": "http.response.body",
            "body": body
        })

mcp_composite_server = FastMCP(name="CharmHealth API Assistant")
mcp_composite_server.add_middleware(ErrorHandlingMiddleware())
mcp_composite_server.add_middleware(SlidingWindowRateLimitingMiddleware(
    max_requests=100,
    window_minutes=1
))
mcp_composite_server.add_middleware(StructuredLoggingMiddleware())
mcp_composite_server.mount(server=core_tools_mcp)
mcp_composite_server.mount(server=patient_management_mcp)
mcp_composite_server.mount(server=scheduling_tools_mcp)
mcp_composite_server.mount(server=encounter_management_mcp)
mcp_composite_server.mount(server=clinical_data_mcp)
mcp_composite_server.mount(server=clinical_support_mcp)
mcp_composite_server.mount(server=task_management_mcp)
mcp_composite_server.mount(server=communication_mcp)

@mcp_composite_server.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> JSONResponse:
    return JSONResponse({"status": "healthy", "timestamp": datetime.now(timezone.utc).isoformat()})

@mcp_composite_server.custom_route("/metrics", methods=["GET"])
async def metrics_endpoint(request: Request) -> Response:
    body, content_type = telemetry.generate_metrics()
    return Response(content=body, media_type=content_type)

formatter = logging.Formatter(
    "%(asctime)s - %(levelname)s - %(name)s: %(lineno)d - %(message)s"
)
console_handler = logging.StreamHandler(sys.stderr)
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(formatter)
logging.basicConfig(level=logging.INFO if os.getenv("ENV") == "prod" else logging.DEBUG, handlers=[console_handler], force=True)
logger = logging.getLogger(__name__)

telemetry.initialize()

if __name__ == "__main__":
    use_stdio = True
    if len(sys.argv) > 1 and sys.argv[1].strip().lower() == 'http':
        use_stdio = False
    if use_stdio:
        mcp_composite_server.run()
    else:
        import uvicorn
        host = os.getenv("MCPSERVER_HOST", "0.0.0.0")
        port = int(os.getenv("MCPSERVER_PORT", "8080"))
        app = mcp_composite_server.http_app(path="/mcp", stateless_http=True)
        wrapped_app = BearerAuthMiddleware(app)
        uvicorn.run(wrapped_app, host=host, port=port)
