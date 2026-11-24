# Copyright 2026 Booz Allen Hamilton Inc.
# SPDX-License-Identifier: Apache-2.0
"""
Tests for the foundry-temporal error handlers.

Each handler must answer its status with the structured ErrorResponse body,
including a JSON-encoded timestamp, not a plain-text 500.
"""

from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from foundry_temporal.errors import ConfigurationError, ValidationError, WorkflowError

from src.main import add_foundry_error_handlers


@pytest.fixture
def client() -> TestClient:
    """An app with only the foundry error handlers and one route per error type."""
    app = FastAPI()
    add_foundry_error_handlers(app)

    @app.get("/workflow-error")
    async def raise_workflow_error() -> None:
        raise WorkflowError("boom", non_retryable=False)

    @app.get("/validation-error")
    async def raise_validation_error() -> None:
        raise ValidationError("bad input")

    @app.get("/configuration-error")
    async def raise_configuration_error() -> None:
        raise ConfigurationError("missing setting")

    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    ("path", "status", "error_type", "error_code", "retryable"),
    [
        ("/workflow-error", 400, "WorkflowError", "WORKFLOW_ERROR", True),
        ("/validation-error", 422, "ValidationError", "VALIDATION_ERROR", False),
        ("/configuration-error", 500, "ConfigurationError", "CONFIG_ERROR", False),
    ],
)
def test_handler_returns_structured_error(
    client: TestClient,
    path: str,
    status: int,
    error_type: str,
    error_code: str,
    retryable: bool,
) -> None:
    response = client.get(path)

    assert response.status_code == status
    assert response.headers["content-type"] == "application/json"
    body = response.json()
    assert body["error_type"] == error_type
    assert body["error_code"] == error_code
    assert body["retryable"] is retryable
    assert body["detail"] is None
    datetime.fromisoformat(body["timestamp"])
