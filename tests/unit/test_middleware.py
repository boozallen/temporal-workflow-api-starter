# Copyright 2026 Booz Allen Hamilton Inc.
# SPDX-License-Identifier: Apache-2.0
"""Tests for the request body size limit (STIG V-222612)."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from foundry_agent_core import _MAX_DICT_BYTES
from temporalio.client import Client

from src.middleware import RequestBodyLimitMiddleware, _declared_content_length

if TYPE_CHECKING:
    from collections.abc import Iterator

    from starlette.types import Message, Receive, Scope, Send

LIMIT = 1024


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestBodyLimitMiddleware, max_body_bytes=LIMIT)

    @app.post("/echo")
    async def echo(request: Request) -> dict[str, int]:
        return {"received": len(await request.body())}

    return app


def _chunks(total: int, size: int = 256) -> Iterator[bytes]:
    while total > 0:
        yield b"a" * min(size, total)
        total -= size


async def _run_asgi(
    headers: list[tuple[bytes, bytes]], chunks: list[bytes]
) -> tuple[list[Message], list[Message]]:
    """Drive the middleware directly; return (messages sent, messages the app received)."""
    incoming: list[Message] = [
        {"type": "http.request", "body": chunk, "more_body": i < len(chunks) - 1}
        for i, chunk in enumerate(chunks)
    ]
    incoming.reverse()
    sent: list[Message] = []
    app_received: list[Message] = []

    async def receive() -> Message:
        return incoming.pop() if incoming else {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        sent.append(message)

    async def inner(scope: Scope, inner_receive: Receive, inner_send: Send) -> None:
        while True:
            message = await inner_receive()
            app_received.append(message)
            if not message.get("more_body", False):
                break
        await inner_send({"type": "http.response.start", "status": 200, "headers": []})
        await inner_send({"type": "http.response.body", "body": b""})

    middleware = RequestBodyLimitMiddleware(inner, max_body_bytes=LIMIT)
    await middleware({"type": "http", "headers": headers}, receive, send)
    return sent, app_received


class TestRequestBodyLimitMiddleware:
    """Bodies over the limit get 413 whether or not Content-Length is declared."""

    def test_declared_body_over_limit_rejected(self):
        with TestClient(_build_app()) as client:
            response = client.post("/echo", content=b"a" * (LIMIT + 1))
        assert response.status_code == 413
        assert response.json() == {"detail": "Request body too large"}

    def test_chunked_body_over_limit_rejected(self):
        with TestClient(_build_app()) as client:
            response = client.post("/echo", content=_chunks(LIMIT + 1))
        assert response.status_code == 413

    def test_body_at_limit_accepted(self):
        with TestClient(_build_app()) as client:
            response = client.post("/echo", content=b"a" * LIMIT)
        assert response.status_code == 200
        assert response.json() == {"received": LIMIT}

    def test_chunked_body_at_limit_replayed(self):
        with TestClient(_build_app()) as client:
            response = client.post("/echo", content=_chunks(LIMIT))
        assert response.status_code == 200
        assert response.json() == {"received": LIMIT}

    async def test_small_content_length_does_not_bypass_limit(self):
        """A body larger than its declared Content-Length is still counted and rejected."""
        sent, app_received = await _run_asgi(
            [(b"content-length", b"1"), (b"transfer-encoding", b"chunked")],
            list(_chunks(LIMIT + 1)),
        )
        assert sent[0]["status"] == 413
        assert app_received == []

    async def test_fragmented_body_replayed_as_one_message(self):
        """Many tiny body messages reach the app as one coalesced message."""
        sent, app_received = await _run_asgi(
            [(b"transfer-encoding", b"chunked")], [b"a"] * LIMIT
        )
        assert sent[0]["status"] == 200
        assert app_received == [
            {"type": "http.request", "body": b"a" * LIMIT, "more_body": False}
        ]

    async def test_disconnect_before_full_body_skips_app(self):
        """A client that disconnects mid-body never reaches the app."""
        sent, app_received = await _run_asgi([(b"transfer-encoding", b"chunked")], [])
        assert sent == []
        assert app_received == []

    def test_malformed_or_negative_content_length_is_ignored(self):
        for value in (b"abc", b"-1"):
            assert (
                _declared_content_length({"headers": [(b"content-length", value)]})
                is None
            )
        assert _declared_content_length({"headers": [(b"content-length", b"5")]}) == 5

    def test_app_enforces_configured_limit(self):
        """create_app() wires MAX_REQUEST_BODY_BYTES into the middleware."""
        from src.main import create_app

        with patch.dict(os.environ, {"MAX_REQUEST_BODY_BYTES": "64"}):
            configured_app = create_app()

        # /health/ needs no auth and only allows GET, so a body the middleware
        # lets through gets the route's own 405 regardless of token config.
        with TestClient(configured_app) as client:
            over = client.post("/health/", content=b"a" * 65)
            within = client.post("/health/", content=b"a" * 64)
        assert over.status_code == 413
        assert within.status_code == 405

    def test_oversized_start_request_rejected_by_app(self):
        """The real app rejects an oversized /workflows/start before the handler runs."""
        from src.dependencies import get_temporal_client
        from src.main import app

        mock_client = AsyncMock(spec=Client)

        async def mock_get_client():
            return mock_client

        app.dependency_overrides[get_temporal_client] = mock_get_client
        try:
            with TestClient(app) as client:
                response = client.post(
                    "/workflows/start",
                    content=b'{"workflow_type": "MyWorkflow", "input_data": {"t": "'
                    + b"a" * _MAX_DICT_BYTES
                    + b'"}}',
                    headers={"Content-Type": "application/json"},
                )
            assert response.status_code == 413
            mock_client.start_workflow.assert_not_called()
        finally:
            app.dependency_overrides.pop(get_temporal_client, None)

    def test_oversized_chunked_start_request_rejected_by_app(self):
        """The real app counts chunked bodies too, not just a declared Content-Length."""
        from src.dependencies import get_temporal_client
        from src.main import app

        mock_client = AsyncMock(spec=Client)

        async def mock_get_client():
            return mock_client

        app.dependency_overrides[get_temporal_client] = mock_get_client
        try:
            with TestClient(app) as client:
                response = client.post(
                    "/workflows/start",
                    content=_chunks(_MAX_DICT_BYTES + 1, size=65_536),
                    headers={"Content-Type": "application/json"},
                )
            assert response.status_code == 413
            mock_client.start_workflow.assert_not_called()
        finally:
            app.dependency_overrides.pop(get_temporal_client, None)
