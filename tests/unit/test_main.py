# Copyright 2026 Booz Allen Hamilton Inc.
# SPDX-License-Identifier: Apache-2.0
"""Tests for proxy-aware client IP resolution."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware


def _build_app(trusted_hosts: str) -> FastAPI:
    app = FastAPI()
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=trusted_hosts)

    @app.get("/client-host")
    async def client_host(request: Request) -> dict[str, str | None]:
        return {"host": request.client.host if request.client else None}

    return app


class TestProxyHeadersMiddleware:
    """Only a trusted proxy's X-Forwarded-For should change request.client.host."""

    def test_forwarded_for_ignored_from_untrusted_proxy(self):
        app = _build_app(trusted_hosts="127.0.0.1")

        with TestClient(app) as client:
            response = client.get(
                "/client-host", headers={"X-Forwarded-For": "203.0.113.9"}
            )

        assert response.json()["host"] == "testclient"

    def test_forwarded_for_honored_from_trusted_proxy(self):
        app = _build_app(trusted_hosts="testclient")

        with TestClient(app) as client:
            response = client.get(
                "/client-host", headers={"X-Forwarded-For": "203.0.113.9"}
            )

        assert response.json()["host"] == "203.0.113.9"
