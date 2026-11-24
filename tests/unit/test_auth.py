# Copyright 2026 Booz Allen Hamilton Inc.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the workflow API's bearer-token auth dependency."""

import pytest
from fastapi.testclient import TestClient

TEST_TOKEN = "test-secret-token-0123456789abcdef"  # noqa: S105
WRONG_TOKEN_HEADERS = {"Authorization": "Bearer wrong-token"}


@pytest.fixture
def app_with_real_auth():
    """Yield the app with conftest's auth override removed so the real dependency runs."""
    from src.dependencies import require_api_key
    from src.main import app

    app.dependency_overrides.pop(require_api_key, None)
    yield app
    app.dependency_overrides.pop(require_api_key, None)


class TestWorkflowRouterAuth:
    """Regression: /workflows/* must reject unauthenticated callers (f002)."""

    def test_approve_and_signal_reject_unauthenticated(
        self, app_with_real_auth, monkeypatch
    ):
        """HITL approve/signal endpoints return 401 without a valid bearer token."""
        monkeypatch.setenv("API_AUTH_TOKEN", TEST_TOKEN)

        with TestClient(app_with_real_auth) as client:
            # No Authorization header -> 401, signal never reaches Temporal.
            r = client.post("/workflows/wf-1/approve")
            assert r.status_code == 401
            r = client.post("/workflows/wf-1/signal", json={"signal_name": "approve"})
            assert r.status_code == 401
            r = client.post(
                "/workflows/wf-1/approve",
                headers=WRONG_TOKEN_HEADERS,
            )
            assert r.status_code == 401

    def test_repeated_failed_attempts_are_throttled(
        self, app_with_real_auth, monkeypatch
    ):
        """Repeated failed bearer-token attempts from one client get 429."""
        monkeypatch.setenv("API_AUTH_TOKEN", TEST_TOKEN)

        with TestClient(app_with_real_auth) as client:
            responses = [
                client.post(
                    "/workflows/wf-1/approve",
                    headers=WRONG_TOKEN_HEADERS,
                )
                for _ in range(6)
            ]
            assert [r.status_code for r in responses[:5]] == [401] * 5
            assert responses[5].status_code == 429

    def test_lockout_blocks_valid_token_too(self, app_with_real_auth, monkeypatch):
        """Once locked out, even a correct token gets 429."""
        monkeypatch.setenv("API_AUTH_TOKEN", TEST_TOKEN)

        with TestClient(app_with_real_auth) as client:
            for _ in range(5):
                client.post(
                    "/workflows/wf-1/approve",
                    headers=WRONG_TOKEN_HEADERS,
                )
            r = client.post(
                "/workflows/wf-1/approve",
                headers={"Authorization": f"Bearer {TEST_TOKEN}"},
            )
            assert r.status_code == 429

    def test_valid_token_is_never_throttled(self, app_with_real_auth, monkeypatch):
        """A client presenting a valid token repeatedly is not rate limited."""
        monkeypatch.setenv("API_AUTH_TOKEN", TEST_TOKEN)

        with TestClient(app_with_real_auth) as client:
            for _ in range(10):
                r = client.post(
                    "/workflows/wf-1/approve",
                    headers={"Authorization": f"Bearer {TEST_TOKEN}"},
                )
                assert r.status_code not in (401, 429)

    def test_throttled_client_does_not_lock_out_other_clients(
        self, app_with_real_auth, monkeypatch
    ):
        """Throttling one client never locks the shared token for other clients."""
        monkeypatch.setenv("API_AUTH_TOKEN", TEST_TOKEN)

        with TestClient(app_with_real_auth, client=("203.0.113.10", 50000)) as attacker:
            for _ in range(5):
                attacker.post(
                    "/workflows/wf-1/approve",
                    headers=WRONG_TOKEN_HEADERS,
                )
            r = attacker.post(
                "/workflows/wf-1/approve",
                headers={"Authorization": f"Bearer {TEST_TOKEN}"},
            )
            assert r.status_code == 429

        with TestClient(app_with_real_auth, client=("203.0.113.20", 50000)) as other:
            r = other.post(
                "/workflows/wf-1/approve",
                headers={"Authorization": f"Bearer {TEST_TOKEN}"},
            )
            assert r.status_code not in (401, 429)

    def test_workflow_routes_fail_closed_without_configured_token(
        self, app_with_real_auth, monkeypatch
    ):
        """With no API_AUTH_TOKEN configured, every workflow request gets 503."""
        import src.config
        from src.config import AppConfig

        # Build the config without .env so a developer's local token can't leak in.
        monkeypatch.delenv("API_AUTH_TOKEN", raising=False)
        monkeypatch.setattr(
            src.config, "_config", AppConfig(api_auth_token=None, _env_file=None)
        )

        with TestClient(app_with_real_auth) as client:
            for headers in ({}, {"Authorization": "Bearer any-presented-token"}):
                responses = [
                    client.get("/workflows/", headers=headers),
                    client.post(
                        "/workflows/wf-1/approve",
                        json={
                            "run_id": "run-1",
                            "approver_id": "reviewer",
                            "content_digest": "digest",
                        },
                        headers=headers,
                    ),
                    client.post(
                        "/workflows/wf-1/signal",
                        json={"signal_name": "approve"},
                        headers=headers,
                    ),
                ]
                assert [r.status_code for r in responses] == [503] * 3
                # 503 can also mean Temporal is unreachable; pin it to the auth check.
                assert {r.json()["detail"] for r in responses} == {
                    "API authentication is not configured (set API_AUTH_TOKEN)"
                }
