# Copyright 2026 Booz Allen Hamilton Inc.
# SPDX-License-Identifier: Apache-2.0
"""
Pytest configuration and fixtures for temporal workflow API tests.
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

if TYPE_CHECKING:
    from collections.abc import Generator

import pytest
import pytest_asyncio
from temporalio.testing import WorkflowEnvironment

from src.config import AppConfig

TEMPORAL_TLS_ENV_VARS = (
    "TEMPORAL_TLS_MODE",
    "TEMPORAL_TLS_CERT",
    "TEMPORAL_TLS_KEY",
    "TEMPORAL_TLS_CA",
)

# src/main.py builds the app when it is imported, which happens during test
# collection, before any fixture runs. Clear inherited TLS settings here too, so
# a shell with TEMPORAL_TLS_MODE=mtls and no local certs can still collect tests.
for _name in TEMPORAL_TLS_ENV_VARS:
    os.environ.pop(_name, None)


@pytest.fixture(scope="session")
def event_loop() -> Generator:
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def app_config() -> AppConfig:
    """Create test configuration."""
    # Use test environment variables
    os.environ["TEMPORAL_HOST"] = "localhost:7233"
    os.environ["TEMPORAL_NAMESPACE"] = "default"
    os.environ["TEMPORAL_TASK_QUEUE"] = "test-workflow-api-queue"
    os.environ["API_HOST"] = "0.0.0.0"
    os.environ["API_PORT"] = "8000"

    return AppConfig()


@pytest_asyncio.fixture
async def workflow_environment():
    """Create a Temporal test environment for workflow testing."""
    env = await WorkflowEnvironment.start_time_skipping()
    try:
        yield env
    finally:
        await env.shutdown()


@pytest.fixture
def mock_temporal_client():
    """Create a mock Temporal client for testing."""
    mock_client = AsyncMock()
    mock_client.get_workflow_handle.return_value = AsyncMock()
    mock_client.start_workflow.return_value = AsyncMock()
    mock_client.describe_namespace.return_value = AsyncMock()
    mock_client.list_workflows.return_value = AsyncMock()
    return mock_client


@pytest.fixture(autouse=True)
def reset_config_cache():
    """Reset the global config cache before each test."""
    import src.config

    src.config._config = None
    yield
    src.config._config = None


@pytest.fixture(autouse=True)
def clear_temporal_tls_env(monkeypatch):
    """Drop TLS settings inherited from the shell, since AppConfig checks the paths exist."""
    for name in TEMPORAL_TLS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def reset_auth_failure_limiter():
    """Reset the failed-auth-attempt rate limiter state before each test."""
    import src.dependencies

    src.dependencies._auth_failure_limiter.clients = {}
    yield
    src.dependencies._auth_failure_limiter.clients = {}


@pytest.fixture(autouse=True)
def reset_temporal_shutdown_flag():
    """Clear the shutdown flag, so a test that ran shutdown cleanup does not make
    later tests' connects discard their client."""
    import src.dependencies

    src.dependencies._shutting_down = False
    yield
    src.dependencies._shutting_down = False


@pytest.fixture(autouse=True)
def reset_probe_timeout_count():
    """Clear the probe timeout count, so one test's timeouts never count in another."""
    import src.dependencies

    src.dependencies._probe_timeout_client = None
    src.dependencies._probe_timeouts = 0
    yield
    src.dependencies._probe_timeout_client = None
    src.dependencies._probe_timeouts = 0


@pytest.fixture(autouse=True)
def override_auth_dependency():
    """Bypass require_api_key for unit tests; auth regression tests opt out."""
    from src.dependencies import require_api_key
    from src.main import app

    app.dependency_overrides[require_api_key] = lambda: None
    yield
    # Tests may have already called .clear(); pop is a safe no-op then.
    app.dependency_overrides.pop(require_api_key, None)


@pytest.fixture
def tls_pem_files(tmp_path) -> dict[str, str]:
    """Readable placeholder PEM files, since AppConfig checks TLS paths exist."""
    paths = {}
    for key, name in (("cert", "client.pem"), ("key", "client.key"), ("ca", "ca.pem")):
        path = tmp_path / name
        path.write_bytes(f"-----{name}-----".encode())
        paths[key] = str(path)
    return paths


# Test markers
pytest_plugins: list[str] = []


def pytest_configure(config):
    """Configure pytest with custom markers."""
    config.addinivalue_line("markers", "unit: Unit tests")
    config.addinivalue_line("markers", "integration: Integration tests")
    config.addinivalue_line("markers", "slow: Slow running tests")
    config.addinivalue_line("markers", "api: API endpoint tests")
