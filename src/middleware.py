# Copyright 2026 Booz Allen Hamilton Inc.
# SPDX-License-Identifier: Apache-2.0
"""
Request body size limit (STIG V-222612 / CCI-002824).

Uvicorn accepts request bodies of any size, so without this middleware an
oversized payload is fully buffered and parsed before field validation runs.
The body is read here, up to the limit, and replayed to the app as one message.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

from fastapi.responses import JSONResponse

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RequestBodyLimitMiddleware:
    """Reject HTTP requests whose body is larger than max_body_bytes with 413."""

    def __init__(self, app: ASGIApp, max_body_bytes: int) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        content_length = _declared_content_length(scope)
        if content_length is not None and content_length > self.max_body_bytes:
            await _reject(scope, receive, send)
            return

        # Count the bytes actually received for every request: a small
        # Content-Length can't be trusted, since some servers (h11) frame the
        # body by Transfer-Encoding: chunked when both headers are sent.
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] != "http.request":
                return  # client disconnected before sending the full body
            body += message.get("body", b"")
            if len(body) > self.max_body_bytes:
                await _reject(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        # Replay the body as a single message, then hand later receive()
        # calls (e.g. http.disconnect) back to the server.
        pending: list[Message] = [
            {"type": "http.request", "body": bytes(body), "more_body": False}
        ]

        async def replay() -> Message:
            if pending:
                return pending.pop()
            return await receive()

        await self.app(scope, replay, send)


def _declared_content_length(scope: Scope) -> int | None:
    """Return the request's Content-Length, or None if absent, malformed, or negative."""
    for name, value in scope.get("headers", []):
        if name == b"content-length":
            try:
                length = int(value)
            except ValueError:
                return None
            return length if length >= 0 else None
    return None


async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
    response = JSONResponse(
        {"detail": "Request body too large"},
        # stdlib name: starlette's HTTP_413_* constants were renamed across versions.
        status_code=HTTPStatus.CONTENT_TOO_LARGE,
    )
    await response(scope, receive, send)
