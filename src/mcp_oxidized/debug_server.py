"""ASGI entry point with authentication header diagnostics."""

from __future__ import annotations

import logging
import os

import uvicorn
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from mcp_oxidized.server import mcp


logger = logging.getLogger(__name__)


class RequestAuthDebugMiddleware(BaseHTTPMiddleware):
    """Log authentication headers for every MCP HTTP request."""

    async def dispatch(self, request: Request, call_next):
        authorization = request.headers.get("authorization", "").strip()
        session_id = request.headers.get("mcp-session-id", "")

        logger.info(
            "MCP request auth debug: method=%s path=%s authorization=%r "
            "session_id=%r header_names=%s",
            request.method,
            request.url.path,
            authorization,
            session_id,
            ",".join(sorted(request.headers.keys())),
        )
        return await call_next(request)


app = mcp.http_app(transport="streamable-http")
app.add_middleware(RequestAuthDebugMiddleware)


def main() -> None:
    """Run the MCP server with HTTP request authentication diagnostics."""
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper())
    port = int(os.environ.get("MCP_PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_config=None)


if __name__ == "__main__":
    main()
