"""Notes how every API request ended, for the monitor and the tracing server.

Two things come out of it (DST-32):
- one line per answered request in the monitor's request log (status only), which
  is how the monitor knows the share of requests that ended in a server error;
- for a server error, an `api-error` event in Langfuse naming the exception class
  and the route pattern. Never the URL, query, headers, body or message.

A pure ASGI middleware, not Starlette's BaseHTTPMiddleware: that one buffers the
response, which would break the player's byte-range streaming of a film.
"""
from __future__ import annotations
from typing import Optional

from shared import monitoring, tracing

# Probes run every few seconds and say nothing about what people are doing.
PROBES = ("/health", "/ready")


class RequestLogMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") in PROBES:
            await self.app(scope, receive, send)
            return

        status: Optional[int] = None

        async def noting_send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, noting_send)
        except Exception as e:
            # Nothing caught it, so Starlette answers 500. An error after the
            # response had started is an error too, whatever status went out.
            _note(scope, 500, type(e).__name__)
            raise
        if status is not None:
            _note(scope, status, None)


def _note(scope, status: int, error: Optional[str]) -> None:
    """Never lets bookkeeping touch the response."""
    try:
        monitoring.log_request(status)
        if status >= 500:
            # The route is set by the router while the request is handled; an
            # unmatched path has none, and is then recorded without one.
            route = getattr(scope.get("route"), "path", None)
            tracing.record_api_error(error or f"HTTP{status}", route, scope.get("method"), status)
    except Exception:  # noqa: BLE001
        pass
