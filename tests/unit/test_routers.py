# Copyright 2026 Booz Allen Hamilton Inc.
# SPDX-License-Identifier: Apache-2.0
"""
Unit tests for FastAPI routers

Teams should customize these tests for their specific router requirements.
"""

import asyncio
import re
import threading
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi.testclient import TestClient
from temporalio.client import Client
from temporalio.service import RPCError, RPCStatusCode

from src.main import app
from src.models import (
    WorkflowStatus,
)


class TestHealthRouter:
    """Test health router endpoints"""

    def test_health_check_success(self):
        """Test successful health check"""
        with TestClient(app) as client:
            response = client.get("/health/")
            assert response.status_code == 200

            data = response.json()
            assert data["status"] == "healthy"
            assert "timestamp" in data
            assert "version" in data

    def test_health_ready_in_mock_mode(self, monkeypatch):
        """Mock mode reports ready and warns, without contacting Temporal."""
        import src.dependencies

        monkeypatch.setenv("TESTING", "true")
        monkeypatch.setenv("ENVIRONMENT", "development")
        monkeypatch.setattr(src.dependencies, "_client_cache", None)
        mock_logger = Mock()
        mock_logger.bind.return_value = mock_logger

        with (
            patch("src.dependencies.create_temporal_client") as mock_create,
            patch("foundry_temporal.logging.get_logger", return_value=mock_logger),
            TestClient(app) as client,
        ):
            response = client.get("/health/ready")

        mock_create.assert_not_called()
        mock_logger.warning.assert_called_once()
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["dependencies"] == {"temporal": "test_mode"}

    def test_health_ready_evicts_cache_on_connection_failure(self):
        """RPCError UNAVAILABLE during the readiness probe evicts the
        cached client."""
        from temporalio.service import RPCError, RPCStatusCode

        import src.dependencies
        from src.dependencies import get_temporal_client
        from src.main import app

        failing_client = Mock()

        async def failing_list_workflows(*args, **kwargs):
            raise RPCError("server unavailable", RPCStatusCode.UNAVAILABLE, b"")
            yield  # pragma: no cover - makes this an async generator function

        failing_client.list_workflows = failing_list_workflows

        async def mock_get_client():
            return failing_client

        src.dependencies._client_cache = failing_client
        app.dependency_overrides[get_temporal_client] = mock_get_client

        try:
            with TestClient(app) as client:
                response = client.get("/health/ready")
                assert response.status_code == 503
                assert response.json()["status"] == "unhealthy"
                # Checked before the client exits: app shutdown clears the cache.
                assert src.dependencies._client_cache is None
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.parametrize("path", ["/health/detailed", "/health/temporal"])
    def test_other_health_routes_evict_cache_on_connection_failure(self, path):
        """The shared probe evicts on a connection failure, so /health/detailed
        and /health/temporal recover too, not only /health/ready."""
        from temporalio.service import RPCError, RPCStatusCode

        import src.dependencies
        from src.main import app

        failing_client = Mock()

        async def failing_list_workflows(*args, **kwargs):
            raise RPCError("server unavailable", RPCStatusCode.UNAVAILABLE, b"")
            yield  # pragma: no cover - makes this an async generator function

        failing_client.list_workflows = failing_list_workflows
        src.dependencies._client_cache = failing_client

        try:
            with TestClient(app) as client:
                response = client.get(path)
                assert response.status_code == 200
                # Checked before the client exits: app shutdown clears the cache.
                assert src.dependencies._client_cache is None
        finally:
            src.dependencies._client_cache = None

    def test_health_ready_keeps_cache_on_request_level_error(self):
        """RPCError PERMISSION_DENIED during the readiness probe does not
        evict the cached client."""
        from temporalio.service import RPCError, RPCStatusCode

        import src.dependencies
        from src.dependencies import get_temporal_client
        from src.main import app

        failing_client = Mock()

        async def failing_list_workflows(*args, **kwargs):
            raise RPCError("permission denied", RPCStatusCode.PERMISSION_DENIED, b"")
            yield  # pragma: no cover - makes this an async generator function

        failing_client.list_workflows = failing_list_workflows

        async def mock_get_client():
            return failing_client

        src.dependencies._client_cache = failing_client
        app.dependency_overrides[get_temporal_client] = mock_get_client

        try:
            with TestClient(app) as client:
                response = client.get("/health/ready")
                assert response.status_code == 200
                assert response.json()["status"] == "healthy"
                assert response.json()["dependencies"] == {"temporal": "degraded"}
                # Checked before the client exits: app shutdown clears the cache.
                assert src.dependencies._client_cache is failing_client
        finally:
            app.dependency_overrides.clear()

    def test_health_ready_hung_probe_times_out_as_unhealthy(self, monkeypatch):
        """A probe RPC that never returns is cut off at the probe timeout and
        reported unhealthy, instead of hanging past the Kubernetes probe."""
        import asyncio
        import time

        import src.dependencies
        from src.main import app

        hung_client = Mock()

        async def hung_list_workflows(*args, **kwargs):
            await asyncio.sleep(30)
            yield  # pragma: no cover - makes this an async generator function

        hung_client.list_workflows = hung_list_workflows
        src.dependencies._client_cache = hung_client
        monkeypatch.setenv("TEMPORAL_PROBE_TIMEOUT_SECONDS", "0.05")

        try:
            with (
                TestClient(app) as client,
            ):
                started = time.monotonic()
                response = client.get("/health/ready")
                elapsed = time.monotonic() - started
        finally:
            src.dependencies._client_cache = None

        assert elapsed < 5
        assert response.status_code == 503
        assert response.json()["status"] == "unhealthy"


class TestWorkflowRouter:
    """Test workflow router endpoints"""

    @pytest.fixture
    def mock_temporal_client(self):
        """Mock Temporal client for testing"""
        client = AsyncMock(spec=Client)
        client.start_workflow = AsyncMock()
        client.get_workflow_handle = AsyncMock()
        client.list_workflows = AsyncMock()
        return client

    def test_start_workflow_no_workflows_available(self):
        """Test starting workflow when no workflows are registered"""
        from src.dependencies import get_temporal_client

        async def mock_get_client():
            return AsyncMock(spec=Client)

        app.dependency_overrides[get_temporal_client] = mock_get_client
        try:
            with TestClient(app) as client:
                response = client.post(
                    "/workflows/start",
                    json={
                        "workflow_type": "NonExistentWorkflow",
                        "input_data": {"test": "data"},
                    },
                )

                assert response.status_code == 400
                data = response.json()
                assert "Unknown workflow type" in data["detail"]
                assert "Available types:" in data["detail"]
        finally:
            app.dependency_overrides.pop(get_temporal_client, None)

    def test_start_workflow_rejects_unallowed_task_queue(self):
        """Test that a caller-supplied task_queue outside the allowlist is rejected"""
        from src.dependencies import get_temporal_client

        async def mock_get_client():
            return AsyncMock()

        app.dependency_overrides[get_temporal_client] = mock_get_client

        try:
            with TestClient(app) as client:
                response = client.post(
                    "/workflows/start",
                    json={
                        "workflow_type": "MyWorkflow",
                        "task_queue": "attacker-controlled-queue",
                        "input_data": {"test": "data"},
                    },
                )

                assert response.status_code == 400
                data = response.json()
                assert "not permitted" in data["detail"]
                assert "attacker-controlled-queue" in data["detail"]
        finally:
            app.dependency_overrides.pop(get_temporal_client, None)

    @patch("src.routers.workflows.AVAILABLE_WORKFLOWS", {"TestWorkflow": Mock()})
    def test_start_workflow_success(self):
        """Test successful workflow start"""
        # Setup mock client and workflow handle
        mock_handle = Mock()
        mock_handle.id = "test-workflow-123"
        mock_handle.result_run_id = "run-456"
        mock_handle.first_execution_run_id = "run-456"

        async def mock_get_client():
            mock_client = Mock()

            # Make start_workflow an async function that returns the handle
            async def mock_start_workflow(*args, **kwargs):
                return mock_handle

            mock_client.start_workflow = mock_start_workflow
            return mock_client

        # Override dependency for this test
        from src.dependencies import get_temporal_client
        from src.main import app

        app.dependency_overrides[get_temporal_client] = mock_get_client

        try:
            with TestClient(app) as client:
                response = client.post(
                    "/workflows/start",
                    json={
                        "workflow_type": "TestWorkflow",
                        "workflow_id": "test-workflow-123",
                        "input_data": {"test": "data"},
                    },
                )

                assert response.status_code == 200
                data = response.json()
                assert data["workflow_id"] == "test-workflow-123"
                assert data["run_id"] == "run-456"
                assert "started_at" in data
        finally:
            # Clean up dependency override
            app.dependency_overrides.clear()

    def test_get_workflow_status_success(self):
        """Test successful workflow status retrieval"""
        # Setup mock client and description
        mock_description = Mock()
        mock_description.status = Mock()
        mock_description.status.name = "RUNNING"
        mock_description.run_id = "run-123"
        mock_description.workflow_type = "MyWorkflow"
        mock_description.task_queue = "workflow-task-queue"
        mock_description.start_time = datetime.now(UTC)
        mock_description.close_time = None
        mock_description.history_length = 10
        mock_description.failure = None

        async def mock_get_client():
            mock_client = Mock()
            mock_handle = Mock()

            # Make describe async
            async def mock_describe(*args, **kwargs):
                return mock_description

            mock_handle.describe = mock_describe
            mock_client.get_workflow_handle.return_value = mock_handle
            return mock_client

        # Override dependency for this test
        from src.dependencies import get_temporal_client
        from src.main import app

        app.dependency_overrides[get_temporal_client] = mock_get_client

        try:
            with TestClient(app) as client:
                response = client.get("/workflows/test-workflow-123/status")

                assert response.status_code == 200
                data = response.json()
                assert data["workflow_id"] == "test-workflow-123"
                assert data["status"] == WorkflowStatus.RUNNING
                assert data["workflow_type"] == "MyWorkflow"
        finally:
            # Clean up dependency override
            app.dependency_overrides.clear()

    def test_get_workflow_status_failed(self):
        """Test workflow status when workflow failed"""
        # Setup mock client with failed workflow
        mock_description = Mock()
        mock_description.status = Mock()
        mock_description.status.name = "FAILED"
        mock_description.run_id = "run-123"
        mock_description.workflow_type = "MyWorkflow"
        mock_description.task_queue = "workflow-task-queue"
        mock_description.start_time = datetime.now(UTC)
        mock_description.close_time = datetime.now(UTC)
        mock_description.history_length = 5

        # Mock failure details
        mock_failure = Mock()
        mock_failure.message = "Test failure message"
        mock_description.failure = mock_failure

        async def mock_get_client():
            mock_client = Mock()
            mock_handle = Mock()

            # Make describe async
            async def mock_describe(*args, **kwargs):
                return mock_description

            mock_handle.describe = mock_describe
            mock_client.get_workflow_handle.return_value = mock_handle
            return mock_client

        # Override dependency for this test
        from src.dependencies import get_temporal_client
        from src.main import app

        app.dependency_overrides[get_temporal_client] = mock_get_client

        try:
            with TestClient(app) as client:
                response = client.get("/workflows/test-workflow-123/status")

                assert response.status_code == 200
                data = response.json()
                assert data["status"] == WorkflowStatus.FAILED
                assert data["error"] == "Test failure message"
        finally:
            # Clean up dependency override
            app.dependency_overrides.clear()

    def test_get_workflow_status_not_found(self):
        """Test workflow status when workflow not found"""
        from src.dependencies import get_temporal_client

        mock_client = AsyncMock(spec=Client)

        def mock_get_workflow_handle(*args, **kwargs):
            raise Exception("Workflow not found")

        mock_client.get_workflow_handle = mock_get_workflow_handle

        async def mock_get_client():
            return mock_client

        app.dependency_overrides[get_temporal_client] = mock_get_client
        try:
            with TestClient(app) as client:
                response = client.get("/workflows/nonexistent-workflow/status")

                assert response.status_code == 404
                data = response.json()
                assert "not found" in data["detail"].lower()
        finally:
            app.dependency_overrides.pop(get_temporal_client, None)

    def test_get_workflow_status_rejects_unmanaged_workflow_type(self):
        """Status for a workflow outside the gateway's managed type fails."""

        async def mock_get_client():
            mock_client = Mock()
            mock_handle = Mock()

            mock_description = Mock()
            mock_description.workflow_type = "SomeoneElsesWorkflow"
            mock_description.task_queue = "workflow-task-queue"

            async def mock_describe(*args, **kwargs):
                return mock_description

            mock_handle.describe = mock_describe
            mock_client.get_workflow_handle.return_value = mock_handle
            return mock_client

        from src.dependencies import get_temporal_client
        from src.main import app

        app.dependency_overrides[get_temporal_client] = mock_get_client
        try:
            with TestClient(app) as client:
                response = client.get("/workflows/other-workflow-123/status")
                assert response.status_code == 404
        finally:
            app.dependency_overrides.pop(get_temporal_client, None)

    def test_get_workflow_status_rejects_unmanaged_task_queue(self):
        """Status for a managed workflow type on an unmanaged task queue fails."""

        async def mock_get_client():
            mock_client = Mock()
            mock_handle = Mock()

            mock_description = Mock()
            mock_description.workflow_type = "MyWorkflow"
            mock_description.task_queue = "someone-elses-queue"

            async def mock_describe(*args, **kwargs):
                return mock_description

            mock_handle.describe = mock_describe
            mock_client.get_workflow_handle.return_value = mock_handle
            return mock_client

        from src.dependencies import get_temporal_client
        from src.main import app

        app.dependency_overrides[get_temporal_client] = mock_get_client
        try:
            with TestClient(app) as client:
                response = client.get("/workflows/other-workflow-123/status")
                assert response.status_code == 404
        finally:
            app.dependency_overrides.pop(get_temporal_client, None)

    def test_list_workflows_success(self):
        """Test successful workflow listing"""
        # Create mock workflow info
        mock_workflow = Mock()
        mock_workflow.id = "workflow-1"
        mock_workflow.run_id = "run-1"
        mock_workflow.workflow_type = "TestWorkflow"
        mock_workflow.status = Mock()
        mock_workflow.status.name = "RUNNING"
        mock_workflow.start_time = datetime.now(UTC)
        mock_workflow.close_time = None
        mock_workflow.task_queue = "test-queue"

        # Mock async iterator
        class AsyncIteratorMock:
            def __init__(self, items):
                self.items = items

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not hasattr(self, "_iter"):
                    self._iter = iter(self.items)
                try:
                    return next(self._iter)
                except StopIteration:
                    raise StopAsyncIteration from None

        async def mock_get_client():
            mock_client = Mock()
            mock_client.list_workflows = Mock(
                return_value=AsyncIteratorMock([mock_workflow])
            )
            return mock_client

        # Override dependency for this test
        from src.dependencies import get_temporal_client
        from src.main import app

        app.dependency_overrides[get_temporal_client] = mock_get_client

        try:
            with TestClient(app) as client:
                response = client.get("/workflows/")

                assert response.status_code == 200
                data = response.json()
                assert "workflows" in data
                assert len(data["workflows"]) == 1
                assert data["workflows"][0]["workflow_id"] == "workflow-1"
        finally:
            # Clean up dependency override
            app.dependency_overrides.clear()

    def test_list_workflows_filters_to_managed_type_and_task_queue(self):
        """The Temporal visibility query is scoped to the managed workflow type and queue."""

        class AsyncIteratorMock:
            def __init__(self, items):
                self.items = items

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not hasattr(self, "_iter"):
                    self._iter = iter(self.items)
                try:
                    return next(self._iter)
                except StopIteration:
                    raise StopAsyncIteration from None

        list_workflows_calls: list[str] = []

        async def mock_get_client():
            mock_client = Mock()

            def mock_list_workflows(query: str, *args, **kwargs):
                list_workflows_calls.append(query)
                return AsyncIteratorMock([])

            mock_client.list_workflows = Mock(side_effect=mock_list_workflows)
            return mock_client

        from src.dependencies import get_temporal_client
        from src.main import app

        app.dependency_overrides[get_temporal_client] = mock_get_client
        try:
            with TestClient(app) as client:
                response = client.get("/workflows/")
                assert response.status_code == 200
                assert len(list_workflows_calls) == 1
                query = list_workflows_calls[0]
                assert 'WorkflowType="MyWorkflow"' in query
                assert 'TaskQueue="workflow-task-queue"' in query
        finally:
            app.dependency_overrides.clear()

    def test_query_workflow_success(self):
        """Test successful workflow query"""

        async def mock_get_client():
            mock_client = Mock()
            mock_handle = Mock()

            mock_description = Mock()
            mock_description.workflow_type = "MyWorkflow"
            mock_description.task_queue = "workflow-task-queue"

            async def mock_describe(*args, **kwargs):
                return mock_description

            # Make query async
            async def mock_query(*args, **kwargs):
                return {"status": "running", "progress": 50}

            mock_handle.describe = mock_describe
            mock_handle.query = mock_query
            mock_client.get_workflow_handle.return_value = mock_handle
            return mock_client

        # Override dependency for this test
        from src.dependencies import get_temporal_client
        from src.main import app

        app.dependency_overrides[get_temporal_client] = mock_get_client

        try:
            with TestClient(app) as client:
                response = client.get(
                    "/workflows/test-workflow-123/query?query_name=get_status"
                )

                assert response.status_code == 200
                data = response.json()
                assert data["workflow_id"] == "test-workflow-123"
                assert data["query_name"] == "get_status"
                assert data["result"]["status"] == "running"
        finally:
            # Clean up dependency override
            app.dependency_overrides.clear()

    def test_query_workflow_rejects_unlisted_query_name(self):
        """Query names outside ALLOWED_QUERIES are rejected before reaching Temporal."""
        from src.dependencies import get_temporal_client

        async def mock_get_client():
            return AsyncMock(spec=Client)

        app.dependency_overrides[get_temporal_client] = mock_get_client
        try:
            with TestClient(app) as client:
                response = client.get(
                    "/workflows/test-workflow-123/query?query_name=internal_debug_dump"
                )
                assert response.status_code == 400
                assert "not permitted" in response.json()["detail"]
        finally:
            app.dependency_overrides.pop(get_temporal_client, None)

    def test_oversized_path_workflow_id_rejected(self):
        """A path workflow_id over 256 characters returns 422 before reaching Temporal."""
        from src.dependencies import get_temporal_client

        mock_client = AsyncMock(spec=Client)

        async def mock_get_client():
            return mock_client

        app.dependency_overrides[get_temporal_client] = mock_get_client
        long_id = "a" * 257
        try:
            with TestClient(app) as client:
                responses = [
                    client.get(f"/workflows/{long_id}/status"),
                    client.get(f"/workflows/{long_id}/query"),
                    client.post(
                        f"/workflows/{long_id}/signal",
                        json={
                            "signal_name": "cancel_workflow",
                            "approver_id": "jane.doe@example.com",
                        },
                    ),
                    client.post(
                        f"/workflows/{long_id}/approve",
                        json={
                            "run_id": "run-123",
                            "approver_id": "jane.doe@example.com",
                            "content_digest": "digest-abc",
                        },
                    ),
                ]
            assert [r.status_code for r in responses] == [422, 422, 422, 422]
            mock_client.get_workflow_handle.assert_not_called()
        finally:
            app.dependency_overrides.pop(get_temporal_client, None)

    def test_oversized_signal_and_approve_bodies_rejected(self):
        """Signal and approve fields over their limits return 422 before reaching Temporal."""
        from src.dependencies import get_temporal_client

        mock_client = AsyncMock(spec=Client)

        async def mock_get_client():
            return mock_client

        app.dependency_overrides[get_temporal_client] = mock_get_client
        approve_body = {
            "run_id": "run-123",
            "approver_id": "jane.doe@example.com",
            "content_digest": "digest-abc",
        }
        try:
            with TestClient(app) as client:
                responses = [
                    client.post(
                        "/workflows/test-workflow-123/signal",
                        json={
                            "signal_name": "a" * 65,
                            "approver_id": "jane.doe@example.com",
                        },
                    ),
                    client.post(
                        "/workflows/test-workflow-123/signal",
                        json={
                            "signal_name": "provide_feedback",
                            "approver_id": "jane.doe@example.com",
                            "comments": "a" * 65_537,
                        },
                    ),
                    client.post(
                        "/workflows/test-workflow-123/approve",
                        json={**approve_body, "approver_id": "a" * 257},
                    ),
                    client.post(
                        "/workflows/test-workflow-123/approve",
                        json={**approve_body, "content_digest": "a" * 129},
                    ),
                ]
            assert [r.status_code for r in responses] == [422, 422, 422, 422]
            mock_client.get_workflow_handle.assert_not_called()
        finally:
            app.dependency_overrides.pop(get_temporal_client, None)

    def test_oversized_query_name_rejected(self):
        """A query_name over 64 characters returns 422."""
        from src.dependencies import get_temporal_client

        async def mock_get_client():
            return AsyncMock(spec=Client)

        app.dependency_overrides[get_temporal_client] = mock_get_client
        try:
            with TestClient(app) as client:
                response = client.get(
                    f"/workflows/test-workflow-123/query?query_name={'a' * 65}"
                )
                assert response.status_code == 422
        finally:
            app.dependency_overrides.pop(get_temporal_client, None)

    def test_query_workflow_rejects_unmanaged_workflow_type(self):
        """Query for a workflow outside the gateway's managed type/queue fails."""

        async def mock_get_client():
            mock_client = Mock()
            mock_handle = Mock()

            mock_description = Mock()
            mock_description.workflow_type = "SomeoneElsesWorkflow"
            mock_description.task_queue = "someone-elses-queue"

            async def mock_describe(*args, **kwargs):
                return mock_description

            mock_handle.describe = mock_describe
            mock_client.get_workflow_handle.return_value = mock_handle
            return mock_client

        from src.dependencies import get_temporal_client
        from src.main import app

        app.dependency_overrides[get_temporal_client] = mock_get_client
        try:
            with TestClient(app) as client:
                response = client.get(
                    "/workflows/other-workflow-123/query?query_name=get_status"
                )
                assert response.status_code == 404
        finally:
            app.dependency_overrides.pop(get_temporal_client, None)

    def test_signal_workflow_success(self):
        """Test successful workflow signal"""

        async def mock_get_client():
            mock_client = Mock()
            mock_handle = Mock()

            mock_description = Mock()
            mock_description.workflow_type = "MyWorkflow"
            mock_description.task_queue = "workflow-task-queue"

            async def mock_describe(*args, **kwargs):
                return mock_description

            # Make signal async
            async def mock_signal(*args, **kwargs):
                return None

            mock_handle.describe = mock_describe
            mock_handle.signal = mock_signal
            mock_client.get_workflow_handle.return_value = mock_handle
            return mock_client

        # Override dependency for this test
        from src.dependencies import get_temporal_client
        from src.main import app

        app.dependency_overrides[get_temporal_client] = mock_get_client

        try:
            with TestClient(app) as client:
                response = client.post(
                    "/workflows/test-workflow-123/signal",
                    json={
                        "signal_name": "provide_feedback",
                        "approver_id": "jane.doe@example.com",
                        "comments": "updated",
                    },
                )

                assert response.status_code == 200
                data = response.json()
                assert data["message"] == "Signal sent"
                assert data["workflow_id"] == "test-workflow-123"
        finally:
            # Clean up dependency override
            app.dependency_overrides.clear()

    def test_signal_workflow_rejects_unlisted_signal_name(self):
        """Signal names outside ALLOWED_SIGNALS are rejected before reaching Temporal."""
        from src.dependencies import get_temporal_client

        async def mock_get_client():
            return AsyncMock(spec=Client)

        app.dependency_overrides[get_temporal_client] = mock_get_client
        try:
            with TestClient(app) as client:
                response = client.post(
                    "/workflows/test-workflow-123/signal",
                    json={
                        "signal_name": "update_status",
                        "approver_id": "jane.doe@example.com",
                    },
                )
                assert response.status_code == 422
                assert "not permitted" in response.json()["detail"]
        finally:
            app.dependency_overrides.pop(get_temporal_client, None)

    def test_signal_workflow_rejects_unmanaged_workflow_type(self):
        """Signal for a workflow outside the gateway's managed type/queue fails."""

        async def mock_get_client():
            mock_client = Mock()
            mock_handle = Mock()

            mock_description = Mock()
            mock_description.workflow_type = "SomeoneElsesWorkflow"
            mock_description.task_queue = "someone-elses-queue"

            async def mock_describe(*args, **kwargs):
                return mock_description

            mock_handle.describe = mock_describe
            mock_client.get_workflow_handle.return_value = mock_handle
            return mock_client

        from src.dependencies import get_temporal_client
        from src.main import app

        app.dependency_overrides[get_temporal_client] = mock_get_client
        try:
            with TestClient(app) as client:
                response = client.post(
                    "/workflows/other-workflow-123/signal",
                    json={
                        "signal_name": "cancel_workflow",
                        "approver_id": "jane.doe@example.com",
                    },
                )
                assert response.status_code == 404
        finally:
            app.dependency_overrides.pop(get_temporal_client, None)

    def _approve_request_body(self, **overrides: str) -> dict[str, str]:
        body = {
            "run_id": "run-123",
            "approver_id": "jane.doe@example.com",
            "content_digest": "digest-abc",
        }
        body.update(overrides)
        return body

    def test_approve_workflow_success(self):
        """Test explicit approve endpoint binds run, approver, and digest."""
        signal_calls: list[tuple] = []

        async def mock_get_client():
            mock_client = Mock()
            mock_handle = Mock()

            mock_description = Mock()
            mock_description.workflow_type = "MyWorkflow"
            mock_description.task_queue = "workflow-task-queue"
            mock_description.run_id = "run-123"

            async def mock_describe(*args, **kwargs):
                return mock_description

            query_responses = iter(
                [
                    {"current_phase": "awaiting_review", "approved_by": None},
                    {
                        "current_phase": "awaiting_review",
                        "approved_by": "jane.doe@example.com",
                    },
                ]
            )

            async def mock_query(*args, **kwargs):
                return next(query_responses)

            async def mock_signal(*args, **kwargs):
                signal_calls.append((args, kwargs))
                return None

            mock_handle.describe = mock_describe
            mock_handle.query = mock_query
            mock_handle.signal = mock_signal
            mock_client.get_workflow_handle.return_value = mock_handle
            return mock_client

        from src.dependencies import get_temporal_client
        from src.main import app

        app.dependency_overrides[get_temporal_client] = mock_get_client

        try:
            with TestClient(app) as client:
                response = client.post(
                    "/workflows/test-workflow-123/approve",
                    json=self._approve_request_body(),
                )

                assert response.status_code == 200
                data = response.json()
                assert data["status"] == "approved"
                assert data["workflow_id"] == "test-workflow-123"
                assert data["run_id"] == "run-123"
                assert len(signal_calls) == 1
                call_args, call_kwargs = signal_calls[0]
                assert call_args == ("approve",)
                assert call_kwargs["args"] == ["jane.doe@example.com", "digest-abc"]
        finally:
            app.dependency_overrides.clear()

    def test_approve_workflow_plan_lane_success(self):
        """lane='plan' signals approve_plan and checks the plan-lane fields."""
        signal_calls: list[tuple] = []

        async def mock_get_client():
            mock_client = Mock()
            mock_handle = Mock()

            mock_description = Mock()
            mock_description.workflow_type = "MyWorkflow"
            mock_description.task_queue = "workflow-task-queue"
            mock_description.run_id = "run-123"

            async def mock_describe(*args, **kwargs):
                return mock_description

            query_responses = iter(
                [
                    {"current_phase": "awaiting_plan_review", "plan_approved_by": None},
                    {
                        "current_phase": "awaiting_plan_review",
                        "plan_approved_by": "jane.doe@example.com",
                    },
                ]
            )

            async def mock_query(*args, **kwargs):
                return next(query_responses)

            async def mock_signal(*args, **kwargs):
                signal_calls.append((args, kwargs))
                return None

            mock_handle.describe = mock_describe
            mock_handle.query = mock_query
            mock_handle.signal = mock_signal
            mock_client.get_workflow_handle.return_value = mock_handle
            return mock_client

        from src.dependencies import get_temporal_client
        from src.main import app

        app.dependency_overrides[get_temporal_client] = mock_get_client

        try:
            with TestClient(app) as client:
                response = client.post(
                    "/workflows/test-workflow-123/approve",
                    json=self._approve_request_body(lane="plan"),
                )

                assert response.status_code == 200
                data = response.json()
                assert data["status"] == "approved"
                assert data["lane"] == "plan"
                call_args, call_kwargs = signal_calls[0]
                assert call_args == ("approve_plan",)
                assert call_kwargs["args"] == ["jane.doe@example.com", "digest-abc"]
        finally:
            app.dependency_overrides.clear()

    def test_approve_workflow_research_lane_success(self):
        """lane='research' signals approve_research and checks the document-lane fields."""
        signal_calls: list[tuple] = []

        async def mock_get_client():
            mock_client = Mock()
            mock_handle = Mock()

            mock_description = Mock()
            mock_description.workflow_type = "MyWorkflow"
            mock_description.task_queue = "workflow-task-queue"
            mock_description.run_id = "run-123"

            async def mock_describe(*args, **kwargs):
                return mock_description

            query_responses = iter(
                [
                    {
                        "current_phase": "awaiting_research_review",
                        "document_approved_by": None,
                    },
                    {
                        "current_phase": "awaiting_research_review",
                        "document_approved_by": "jane.doe@example.com",
                    },
                ]
            )

            async def mock_query(*args, **kwargs):
                return next(query_responses)

            async def mock_signal(*args, **kwargs):
                signal_calls.append((args, kwargs))
                return None

            mock_handle.describe = mock_describe
            mock_handle.query = mock_query
            mock_handle.signal = mock_signal
            mock_client.get_workflow_handle.return_value = mock_handle
            return mock_client

        from src.dependencies import get_temporal_client
        from src.main import app

        app.dependency_overrides[get_temporal_client] = mock_get_client

        try:
            with TestClient(app) as client:
                response = client.post(
                    "/workflows/test-workflow-123/approve",
                    json=self._approve_request_body(lane="research"),
                )

                assert response.status_code == 200
                data = response.json()
                assert data["status"] == "approved"
                assert data["lane"] == "research"
                call_args, call_kwargs = signal_calls[0]
                assert call_args == ("approve_research",)
                assert call_kwargs["args"] == ["jane.doe@example.com", "digest-abc"]
        finally:
            app.dependency_overrides.clear()

    def test_approve_workflow_wrong_lane_phase_fails(self):
        """lane='plan' while only the general lane is awaiting review fails 409."""

        async def mock_get_client():
            mock_client = Mock()
            mock_handle = Mock()

            mock_description = Mock()
            mock_description.workflow_type = "MyWorkflow"
            mock_description.task_queue = "workflow-task-queue"
            mock_description.run_id = "run-123"

            async def mock_describe(*args, **kwargs):
                return mock_description

            async def mock_query(*args, **kwargs):
                return {"current_phase": "awaiting_review", "plan_approved_by": None}

            mock_handle.describe = mock_describe
            mock_handle.query = mock_query
            mock_client.get_workflow_handle.return_value = mock_handle
            return mock_client

        from src.dependencies import get_temporal_client
        from src.main import app

        app.dependency_overrides[get_temporal_client] = mock_get_client
        try:
            with TestClient(app) as client:
                response = client.post(
                    "/workflows/test-workflow-123/approve",
                    json=self._approve_request_body(lane="plan"),
                )
                assert response.status_code == 409
        finally:
            app.dependency_overrides.clear()

    def test_signal_workflow_rejects_approve_signals(self):
        """approve, approve_plan, and approve_research are rejected via /signal."""
        from src.dependencies import get_temporal_client

        async def mock_get_client():
            return AsyncMock(spec=Client)

        app.dependency_overrides[get_temporal_client] = mock_get_client
        try:
            with TestClient(app) as client:
                for signal_name in ("approve", "approve_plan", "approve_research"):
                    response = client.post(
                        "/workflows/test-workflow-123/signal",
                        json={
                            "signal_name": signal_name,
                            "approver_id": "jane.doe@example.com",
                        },
                    )
                    assert response.status_code == 422, signal_name
                    assert "not permitted" in response.json()["detail"]
        finally:
            app.dependency_overrides.pop(get_temporal_client, None)

    def test_approve_workflow_requires_body(self):
        """Approve endpoint requires run_id, approver_id, and content_digest."""
        from src.dependencies import get_temporal_client

        async def mock_get_client():
            return AsyncMock(spec=Client)

        app.dependency_overrides[get_temporal_client] = mock_get_client
        try:
            with TestClient(app) as client:
                response = client.post("/workflows/test-workflow-123/approve")
                assert response.status_code == 422
        finally:
            app.dependency_overrides.pop(get_temporal_client, None)

    def test_approve_workflow_stale_run_id_fails(self):
        """Approve request for a stale/nonexistent run_id fails with 404.

        The handle is pinned to run_id, so the real Temporal SDK raises
        directly from describe() for a stale run_id - it never returns a
        description with a different run_id. Simulate that raise rather
        than a mismatched description.
        """

        async def mock_get_client():
            mock_client = Mock()
            mock_handle = Mock()

            async def mock_describe(*args, **kwargs):
                raise RuntimeError("workflow execution not found")

            mock_handle.describe = mock_describe
            mock_client.get_workflow_handle.return_value = mock_handle
            return mock_client

        from src.dependencies import get_temporal_client
        from src.main import app

        app.dependency_overrides[get_temporal_client] = mock_get_client
        try:
            with TestClient(app) as client:
                response = client.post(
                    "/workflows/test-workflow-123/approve",
                    json=self._approve_request_body(run_id="run-123"),
                )
                assert response.status_code == 404
        finally:
            app.dependency_overrides.clear()

    def test_approve_workflow_not_awaiting_review_fails(self):
        """Approve request for a run that is not awaiting review fails."""

        async def mock_get_client():
            mock_client = Mock()
            mock_handle = Mock()

            mock_description = Mock()
            mock_description.workflow_type = "MyWorkflow"
            mock_description.task_queue = "workflow-task-queue"
            mock_description.run_id = "run-123"

            async def mock_describe(*args, **kwargs):
                return mock_description

            async def mock_query(*args, **kwargs):
                return {"current_phase": "running", "approved_by": None}

            signal_calls: list[tuple] = []

            async def mock_signal(*args, **kwargs):
                signal_calls.append(args)
                return None

            mock_handle.describe = mock_describe
            mock_handle.query = mock_query
            mock_handle.signal = mock_signal
            mock_handle._signal_calls = signal_calls
            mock_client.get_workflow_handle.return_value = mock_handle
            return mock_client

        from src.dependencies import get_temporal_client
        from src.main import app

        app.dependency_overrides[get_temporal_client] = mock_get_client
        try:
            with TestClient(app) as client:
                response = client.post(
                    "/workflows/test-workflow-123/approve",
                    json=self._approve_request_body(),
                )
                assert response.status_code == 409
        finally:
            app.dependency_overrides.clear()

    def test_approve_workflow_digest_mismatch_fails(self):
        """A no-op approve signal (e.g. digest mismatch) must not report success."""

        async def mock_get_client():
            mock_client = Mock()
            mock_handle = Mock()

            mock_description = Mock()
            mock_description.workflow_type = "MyWorkflow"
            mock_description.task_queue = "workflow-task-queue"
            mock_description.run_id = "run-123"

            async def mock_describe(*args, **kwargs):
                return mock_description

            # approved_by never changes - simulates HILSignalsMixin silently
            # no-op'ing the signal on a digest mismatch.
            async def mock_query(*args, **kwargs):
                return {"current_phase": "awaiting_review", "approved_by": None}

            async def mock_signal(*args, **kwargs):
                return None

            mock_handle.describe = mock_describe
            mock_handle.query = mock_query
            mock_handle.signal = mock_signal
            mock_client.get_workflow_handle.return_value = mock_handle
            return mock_client

        from src.dependencies import get_temporal_client
        from src.main import app

        app.dependency_overrides[get_temporal_client] = mock_get_client
        try:
            with TestClient(app) as client:
                response = client.post(
                    "/workflows/test-workflow-123/approve",
                    json=self._approve_request_body(content_digest="wrong-digest"),
                )
                assert response.status_code == 409
        finally:
            app.dependency_overrides.clear()

    def test_approve_workflow_failure(self):
        """Test approve endpoint when Temporal signal fails."""

        async def mock_get_client():
            mock_client = Mock()
            mock_handle = Mock()

            mock_description = Mock()
            mock_description.workflow_type = "MyWorkflow"
            mock_description.task_queue = "workflow-task-queue"
            mock_description.run_id = "run-123"

            async def mock_describe(*args, **kwargs):
                return mock_description

            async def mock_query(*args, **kwargs):
                return {"current_phase": "awaiting_review", "approved_by": None}

            async def mock_signal(*args, **kwargs):
                raise Exception("Workflow not found")

            mock_handle.describe = mock_describe
            mock_handle.query = mock_query
            mock_handle.signal = mock_signal
            mock_client.get_workflow_handle.return_value = mock_handle
            return mock_client

        from src.dependencies import get_temporal_client
        from src.main import app

        app.dependency_overrides[get_temporal_client] = mock_get_client

        try:
            with TestClient(app) as client:
                response = client.post(
                    "/workflows/nonexistent/approve",
                    json=self._approve_request_body(),
                )

                assert response.status_code == 500
                data = response.json()
                assert "Failed to approve workflow" in data["detail"]
        finally:
            app.dependency_overrides.clear()

    def test_signal_workflow_cancel_sends_only_approver_id(self):
        """cancel_workflow (no comments field) passes exactly one positional arg."""
        signal_calls: list[tuple] = []

        async def mock_get_client():
            mock_client = Mock()
            mock_handle = Mock()

            mock_description = Mock()
            mock_description.workflow_type = "MyWorkflow"
            mock_description.task_queue = "workflow-task-queue"

            async def mock_describe(*args, **kwargs):
                return mock_description

            async def mock_signal(*args, **kwargs):
                signal_calls.append((args, kwargs))
                return None

            mock_handle.describe = mock_describe
            mock_handle.signal = mock_signal
            mock_client.get_workflow_handle.return_value = mock_handle
            return mock_client

        from src.dependencies import get_temporal_client
        from src.main import app

        app.dependency_overrides[get_temporal_client] = mock_get_client

        try:
            with TestClient(app) as client:
                response = client.post(
                    "/workflows/test-workflow-123/signal",
                    json={
                        "signal_name": "cancel_workflow",
                        "approver_id": "jane.doe@example.com",
                    },
                )

                assert response.status_code == 200
                assert len(signal_calls) == 1
                call_args, call_kwargs = signal_calls[0]
                assert call_args == ("cancel_workflow",)
                assert call_kwargs["args"] == ["jane.doe@example.com"]
        finally:
            app.dependency_overrides.clear()

    def test_signal_workflow_failure(self):
        """Test workflow signal when it fails"""
        from src.dependencies import get_temporal_client

        mock_client = AsyncMock(spec=Client)
        mock_handle = AsyncMock()

        mock_description = Mock()
        mock_description.workflow_type = "MyWorkflow"
        mock_description.task_queue = "workflow-task-queue"

        async def mock_describe(*args, **kwargs):
            return mock_description

        async def mock_signal(*args, **kwargs):
            raise Exception("Signal failed")

        mock_handle.describe = mock_describe
        mock_handle.signal = mock_signal
        mock_client.get_workflow_handle.return_value = mock_handle

        async def mock_get_client():
            return mock_client

        app.dependency_overrides[get_temporal_client] = mock_get_client
        try:
            with TestClient(app) as client:
                response = client.post(
                    "/workflows/test-workflow-123/signal",
                    json={
                        "signal_name": "provide_feedback",
                        "approver_id": "jane.doe@example.com",
                        "comments": "updated",
                    },
                )

            assert response.status_code == 500
            data = response.json()
            assert "Failed to send signal" in data["detail"]
        finally:
            app.dependency_overrides.pop(get_temporal_client, None)


class TestWorkflowRouterValidation:
    """Test workflow router request validation"""

    def test_start_workflow_invalid_request(self):
        """Test workflow start with invalid request data"""
        from src.dependencies import get_temporal_client

        async def mock_get_client():
            return AsyncMock(spec=Client)

        app.dependency_overrides[get_temporal_client] = mock_get_client
        try:
            with TestClient(app) as client:
                response = client.post("/workflows/start", json={})
                assert response.status_code == 422  # Validation error
        finally:
            app.dependency_overrides.pop(get_temporal_client, None)

    def test_start_workflow_empty_workflow_type(self):
        """Test workflow start with empty workflow type"""
        from src.dependencies import get_temporal_client

        async def mock_get_client():
            return AsyncMock(spec=Client)

        app.dependency_overrides[get_temporal_client] = mock_get_client
        try:
            with TestClient(app) as client:
                response = client.post(
                    "/workflows/start",
                    json={"workflow_type": "", "input_data": {}},
                )
                assert response.status_code == 422  # Validation error
        finally:
            app.dependency_overrides.pop(get_temporal_client, None)

    def test_signal_workflow_invalid_request(self):
        """Test workflow signal with invalid request data"""
        from src.dependencies import get_temporal_client

        async def mock_get_client():
            return AsyncMock(spec=Client)

        app.dependency_overrides[get_temporal_client] = mock_get_client
        try:
            with TestClient(app) as client:
                response = client.post(
                    "/workflows/test-workflow/signal",
                    json={},  # Missing required fields
                )
                assert response.status_code == 422  # Validation error
        finally:
            app.dependency_overrides.pop(get_temporal_client, None)

    def test_list_workflows_rejects_invalid_status_filter(self):
        """Test list workflows rejects status_filter values outside the enum"""
        from src.dependencies import get_temporal_client

        async def mock_get_client():
            return AsyncMock(spec=Client)

        app.dependency_overrides[get_temporal_client] = mock_get_client
        try:
            with TestClient(app) as client:
                response = client.get(
                    "/workflows/",
                    params={"status_filter": 'Running" OR WorkflowType!="x'},
                )
                assert response.status_code == 422  # Validation error
        finally:
            app.dependency_overrides.pop(get_temporal_client, None)


class TestWorkflowRouterErrorHandling:
    """Test workflow router error handling"""

    @patch("src.routers.workflows.AVAILABLE_WORKFLOWS", {"TestWorkflow": Mock()})
    def test_start_workflow_temporal_error(self):
        """Test workflow start when Temporal client fails"""
        from src.dependencies import get_temporal_client

        mock_client = AsyncMock(spec=Client)

        async def mock_start_workflow(*args, **kwargs):
            raise Exception("Temporal connection failed")

        mock_client.start_workflow = mock_start_workflow

        async def mock_get_client():
            return mock_client

        app.dependency_overrides[get_temporal_client] = mock_get_client
        try:
            with TestClient(app) as client:
                response = client.post(
                    "/workflows/start",
                    json={
                        "workflow_type": "TestWorkflow",
                        "input_data": {"test": "data"},
                    },
                )

                assert response.status_code == 500
                data = response.json()
                assert "Failed to start workflow" in data["detail"]
        finally:
            app.dependency_overrides.pop(get_temporal_client, None)

    @patch("src.routers.workflows.AVAILABLE_WORKFLOWS", {"TestWorkflow": Mock()})
    def test_start_workflow_duplicate_id_returns_409(self):
        """A second start with the same workflow_id is a conflict, not a 500."""
        from temporalio.exceptions import WorkflowAlreadyStartedError

        from src.dependencies import get_temporal_client

        mock_client = AsyncMock(spec=Client)
        started: set[str] = set()

        async def mock_start_workflow(*args, id, **kwargs):
            if id in started:
                raise WorkflowAlreadyStartedError(id, "TestWorkflow")
            started.add(id)
            return Mock(id=id, result_run_id="run-1")

        mock_client.start_workflow = mock_start_workflow

        async def mock_get_client():
            return mock_client

        app.dependency_overrides[get_temporal_client] = mock_get_client
        body = {"workflow_type": "TestWorkflow", "workflow_id": "order-abc123"}
        mock_logger = Mock()
        mock_logger.bind.return_value = mock_logger
        try:
            with (
                patch("foundry_temporal.logging.get_logger", return_value=mock_logger),
                TestClient(app) as client,
            ):
                first = client.post("/workflows/start", json=body)
                second = client.post("/workflows/start", json=body)

                assert first.status_code == 200
                assert second.status_code == 409
                assert second.json()["detail"] == (
                    "Workflow order-abc123 already exists"
                )
            mock_logger.warning.assert_any_call("Workflow already exists: order-abc123")
        finally:
            app.dependency_overrides.pop(get_temporal_client, None)

    @patch("src.routers.workflows.AVAILABLE_WORKFLOWS", {"TestWorkflow": Mock()})
    def test_start_workflow_connection_failure_evicts_cache(self):
        """RPCError UNAVAILABLE evicts the cached client."""
        from temporalio.service import RPCError, RPCStatusCode

        import src.dependencies
        from src.dependencies import get_temporal_client

        mock_client = AsyncMock(spec=Client)

        async def failing_start_workflow(*args, **kwargs):
            raise RPCError("server unavailable", RPCStatusCode.UNAVAILABLE, b"")

        mock_client.start_workflow = failing_start_workflow

        async def mock_get_client():
            return mock_client

        src.dependencies._client_cache = mock_client
        app.dependency_overrides[get_temporal_client] = mock_get_client
        try:
            with TestClient(app) as client:
                response = client.post(
                    "/workflows/start",
                    json={
                        "workflow_type": "TestWorkflow",
                        "input_data": {"test": "data"},
                    },
                )
                assert response.status_code == 503
                assert (
                    response.json()["detail"] == "Unable to connect to Temporal server"
                )
                # Checked before the client exits: app shutdown clears the cache.
                assert src.dependencies._client_cache is None
        finally:
            app.dependency_overrides.pop(get_temporal_client, None)

    @patch("src.routers.workflows.AVAILABLE_WORKFLOWS", {"TestWorkflow": Mock()})
    def test_start_workflow_permission_error_keeps_cache(self):
        """RPCError PERMISSION_DENIED does not evict the cached client."""
        from temporalio.service import RPCError, RPCStatusCode

        import src.dependencies
        from src.dependencies import get_temporal_client

        mock_client = AsyncMock(spec=Client)

        async def failing_start_workflow(*args, **kwargs):
            raise RPCError("permission denied", RPCStatusCode.PERMISSION_DENIED, b"")

        mock_client.start_workflow = failing_start_workflow

        async def mock_get_client():
            return mock_client

        src.dependencies._client_cache = mock_client
        app.dependency_overrides[get_temporal_client] = mock_get_client
        try:
            with TestClient(app) as client:
                response = client.post(
                    "/workflows/start",
                    json={
                        "workflow_type": "TestWorkflow",
                        "input_data": {"test": "data"},
                    },
                )
                assert response.status_code == 500
                # Checked before the client exits: app shutdown clears the cache.
                assert src.dependencies._client_cache is mock_client
        finally:
            app.dependency_overrides.pop(get_temporal_client, None)

    def test_get_workflow_status_connection_failure_evicts_cache(self):
        """RPCError UNAVAILABLE from describe() evicts the cached client."""
        from temporalio.service import RPCError, RPCStatusCode

        import src.dependencies
        from src.dependencies import get_temporal_client

        mock_client = Mock()
        mock_handle = Mock()

        async def failing_describe(*args, **kwargs):
            raise RPCError("server unavailable", RPCStatusCode.UNAVAILABLE, b"")

        mock_handle.describe = failing_describe
        mock_client.get_workflow_handle.return_value = mock_handle

        async def mock_get_client():
            return mock_client

        src.dependencies._client_cache = mock_client
        app.dependency_overrides[get_temporal_client] = mock_get_client
        try:
            with TestClient(app) as client:
                response = client.get("/workflows/test-workflow-123/status")
                assert response.status_code == 503
                assert (
                    response.json()["detail"] == "Unable to connect to Temporal server"
                )
                # Checked before the client exits: app shutdown clears the cache.
                assert src.dependencies._client_cache is None
        finally:
            app.dependency_overrides.pop(get_temporal_client, None)

    @staticmethod
    def _completed_status_client(result_error):
        """A client whose workflow is COMPLETED and whose result() raises."""
        mock_description = Mock()
        mock_description.status = Mock()
        mock_description.status.name = "COMPLETED"
        mock_description.run_id = "run-123"
        mock_description.workflow_type = "MyWorkflow"
        mock_description.task_queue = "workflow-task-queue"
        mock_description.start_time = datetime.now(UTC)
        mock_description.close_time = datetime.now(UTC)
        mock_description.history_length = 10
        mock_description.failure = None

        async def mock_describe(*args, **kwargs):
            return mock_description

        async def failing_result(*args, **kwargs):
            raise result_error

        mock_client = Mock()
        mock_handle = Mock()
        mock_handle.describe = mock_describe
        mock_handle.result = failing_result
        mock_client.get_workflow_handle.return_value = mock_handle
        return mock_client

    def test_get_workflow_status_result_connection_failure_returns_503(self):
        """A connection failure while fetching a completed workflow's result
        evicts the cached client and answers 503, instead of COMPLETED with
        an empty result."""
        from temporalio.service import RPCError, RPCStatusCode

        import src.dependencies
        from src.dependencies import get_temporal_client

        mock_client = self._completed_status_client(
            RPCError("server unavailable", RPCStatusCode.UNAVAILABLE, b"")
        )

        async def mock_get_client():
            return mock_client

        src.dependencies._client_cache = mock_client
        app.dependency_overrides[get_temporal_client] = mock_get_client
        try:
            with TestClient(app) as client:
                response = client.get("/workflows/test-workflow-123/status")
                assert response.status_code == 503
                assert (
                    response.json()["detail"] == "Unable to connect to Temporal server"
                )
                # Checked before the client exits: app shutdown clears the cache.
                assert src.dependencies._client_cache is None
        finally:
            app.dependency_overrides.pop(get_temporal_client, None)
            src.dependencies._client_cache = None

    def test_get_workflow_status_undecodable_result_still_returns_status(self):
        """A result that cannot be decoded keeps the existing behavior:
        COMPLETED with an empty result."""
        from src.dependencies import get_temporal_client

        mock_client = self._completed_status_client(ValueError("not serializable"))

        async def mock_get_client():
            return mock_client

        mock_logger = Mock()
        mock_logger.bind.return_value = mock_logger
        app.dependency_overrides[get_temporal_client] = mock_get_client
        try:
            with (
                patch("foundry_temporal.logging.get_logger", return_value=mock_logger),
                TestClient(app) as client,
            ):
                response = client.get("/workflows/test-workflow-123/status")
                assert response.status_code == 200
                assert response.json()["status"] == WorkflowStatus.COMPLETED
                assert response.json()["result"] is None
        finally:
            app.dependency_overrides.pop(get_temporal_client, None)

        assert any(
            "Result could not be read" in call.args[0]
            for call in mock_logger.warning.call_args_list
        )

    def test_get_workflow_status_fetches_result_from_the_described_run(self):
        """The result comes from the run describe() reported, so a newer run with
        the same workflow_id cannot make the request wait on it."""
        from src.dependencies import get_temporal_client

        mock_description = Mock()
        mock_description.status = Mock()
        mock_description.status.name = "COMPLETED"
        mock_description.run_id = "run-123"
        mock_description.workflow_type = "MyWorkflow"
        mock_description.task_queue = "workflow-task-queue"
        mock_description.start_time = datetime.now(UTC)
        mock_description.close_time = datetime.now(UTC)
        mock_description.history_length = 10
        mock_description.failure = None

        latest_handle = Mock()
        latest_handle.describe = AsyncMock(return_value=mock_description)
        latest_handle.result = AsyncMock(side_effect=AssertionError("latest run read"))
        described_run_handle = Mock()
        described_run_handle.result = AsyncMock(return_value={"answer": 42})

        mock_client = Mock()
        mock_client.get_workflow_handle.side_effect = lambda workflow_id, run_id=None: (
            described_run_handle if run_id == "run-123" else latest_handle
        )

        async def mock_get_client():
            return mock_client

        app.dependency_overrides[get_temporal_client] = mock_get_client
        try:
            with TestClient(app) as client:
                response = client.get("/workflows/test-workflow-123/status")
                assert response.status_code == 200
                assert response.json()["result"] == {"answer": 42}
        finally:
            app.dependency_overrides.pop(get_temporal_client, None)

        mock_client.get_workflow_handle.assert_any_call(
            "test-workflow-123", run_id="run-123"
        )
        latest_handle.result.assert_not_called()

    def test_get_workflow_status_does_not_follow_a_cron_run_to_its_next_run(self):
        """A completed cron run's history names its next run. The result is the
        described run's own result, not the next run's, even though the real
        WorkflowHandle.result() follows that link by default."""
        from temporalio.api.common.v1 import Payloads
        from temporalio.api.history.v1 import HistoryEvent
        from temporalio.client import WorkflowHandle
        from temporalio.converter import DataConverter

        from src.dependencies import get_temporal_client

        payload_converter = DataConverter.default.payload_converter

        def completed_event(result: dict, next_run_id: str = "") -> HistoryEvent:
            event = HistoryEvent()
            attrs = event.workflow_execution_completed_event_attributes
            attrs.result.CopyFrom(
                Payloads(payloads=payload_converter.to_payloads([result]))
            )
            attrs.new_execution_run_id = next_run_id
            return event

        history = {
            "run-1": completed_event({"report": "10:00"}, next_run_id="run-2"),
            "run-2": completed_event({"report": "11:00"}),
        }

        async def fetch_close_event(run_id, **kwargs):
            yield history[run_id]

        mock_client = Mock()
        mock_client.data_converter = DataConverter.default
        described_run_handle = WorkflowHandle(
            mock_client, "hourly-report", run_id="run-1", result_run_id="run-1"
        )
        described_run_handle._fetch_history_events_for_run = fetch_close_event

        mock_description = Mock()
        mock_description.status = Mock()
        mock_description.status.name = "COMPLETED"
        mock_description.run_id = "run-1"
        mock_description.workflow_type = "MyWorkflow"
        mock_description.task_queue = "workflow-task-queue"
        mock_description.start_time = datetime.now(UTC)
        mock_description.close_time = datetime.now(UTC)
        mock_description.history_length = 10
        mock_description.failure = None
        latest_handle = Mock()
        latest_handle.describe = AsyncMock(return_value=mock_description)

        mock_client.get_workflow_handle.side_effect = lambda workflow_id, run_id=None: (
            described_run_handle if run_id == "run-1" else latest_handle
        )

        async def mock_get_client():
            return mock_client

        app.dependency_overrides[get_temporal_client] = mock_get_client
        try:
            with TestClient(app) as client:
                response = client.get("/workflows/hourly-report/status")
                assert response.status_code == 200
                assert response.json()["run_id"] == "run-1"
                assert response.json()["result"] == {"report": "10:00"}
        finally:
            app.dependency_overrides.pop(get_temporal_client, None)

    def test_get_workflow_status_result_request_error_keeps_cache_and_warns(self):
        """An RPCError that is not a connection failure, such as NOT_FOUND after
        the history is purged, returns COMPLETED with an empty result, keeps the
        cached client, and logs a warning."""
        from temporalio.service import RPCError, RPCStatusCode

        import src.dependencies
        from src.dependencies import get_temporal_client

        mock_client = self._completed_status_client(
            RPCError("history not found", RPCStatusCode.NOT_FOUND, b"")
        )

        async def mock_get_client():
            return mock_client

        mock_logger = Mock()
        mock_logger.bind.return_value = mock_logger
        src.dependencies._client_cache = mock_client
        app.dependency_overrides[get_temporal_client] = mock_get_client
        try:
            with (
                patch("foundry_temporal.logging.get_logger", return_value=mock_logger),
                TestClient(app) as client,
            ):
                response = client.get("/workflows/test-workflow-123/status")
                assert response.status_code == 200
                assert response.json()["status"] == WorkflowStatus.COMPLETED
                assert response.json()["result"] is None
                # Checked before the client exits: app shutdown clears the cache.
                assert src.dependencies._client_cache is mock_client
        finally:
            app.dependency_overrides.pop(get_temporal_client, None)
            src.dependencies._client_cache = None

        assert any(
            "Result fetch failed" in call.args[0]
            for call in mock_logger.warning.call_args_list
        )

    def test_list_workflows_connection_failure_evicts_cache(self):
        """RPCError UNAVAILABLE from list_workflows() evicts the cached
        client."""
        from temporalio.service import RPCError, RPCStatusCode

        import src.dependencies
        from src.dependencies import get_temporal_client

        mock_client = Mock()

        def failing_list_workflows(*args, **kwargs):
            raise RPCError("server unavailable", RPCStatusCode.UNAVAILABLE, b"")

        mock_client.list_workflows = failing_list_workflows

        async def mock_get_client():
            return mock_client

        src.dependencies._client_cache = mock_client
        app.dependency_overrides[get_temporal_client] = mock_get_client
        try:
            with TestClient(app) as client:
                response = client.get("/workflows/")
                assert response.status_code == 503
                assert (
                    response.json()["detail"] == "Unable to connect to Temporal server"
                )
                # Checked before the client exits: app shutdown clears the cache.
                assert src.dependencies._client_cache is None
        finally:
            app.dependency_overrides.pop(get_temporal_client, None)

    def test_query_workflow_connection_failure_evicts_cache(self):
        """RPCError UNAVAILABLE from describe() (the outer handler, not the
        inner query-specific one) evicts the cached client."""
        from temporalio.service import RPCError, RPCStatusCode

        import src.dependencies
        from src.dependencies import get_temporal_client

        mock_client = Mock()
        mock_handle = Mock()

        async def failing_describe(*args, **kwargs):
            raise RPCError("server unavailable", RPCStatusCode.UNAVAILABLE, b"")

        mock_handle.describe = failing_describe
        mock_client.get_workflow_handle.return_value = mock_handle

        async def mock_get_client():
            return mock_client

        src.dependencies._client_cache = mock_client
        app.dependency_overrides[get_temporal_client] = mock_get_client
        try:
            with TestClient(app) as client:
                response = client.get(
                    "/workflows/test-workflow-123/query?query_name=get_status"
                )
                assert response.status_code == 503
                assert (
                    response.json()["detail"] == "Unable to connect to Temporal server"
                )
                # Checked before the client exits: app shutdown clears the cache.
                assert src.dependencies._client_cache is None
        finally:
            app.dependency_overrides.pop(get_temporal_client, None)

    def test_query_workflow_inner_query_connection_failure_returns_503(self):
        """A connection failure from the query itself, after describe()
        succeeds, answers 503 instead of 200 with "Query failed"."""
        from temporalio.service import RPCError, RPCStatusCode

        import src.dependencies
        from src.dependencies import get_temporal_client

        mock_client = Mock()
        mock_handle = Mock()
        mock_description = Mock()
        mock_description.workflow_type = "MyWorkflow"
        mock_description.task_queue = "workflow-task-queue"

        async def mock_describe(*args, **kwargs):
            return mock_description

        async def failing_query(*args, **kwargs):
            raise RPCError("server unavailable", RPCStatusCode.UNAVAILABLE, b"")

        mock_handle.describe = mock_describe
        mock_handle.query = failing_query
        mock_client.get_workflow_handle.return_value = mock_handle

        async def mock_get_client():
            return mock_client

        src.dependencies._client_cache = mock_client
        app.dependency_overrides[get_temporal_client] = mock_get_client
        try:
            with TestClient(app) as client:
                response = client.get(
                    "/workflows/test-workflow-123/query?query_name=get_status"
                )
                assert response.status_code == 503
                assert (
                    response.json()["detail"] == "Unable to connect to Temporal server"
                )
                # Checked before the client exits: app shutdown clears the cache.
                assert src.dependencies._client_cache is None
        finally:
            app.dependency_overrides.pop(get_temporal_client, None)

    def test_get_workflow_status_not_found_still_returns_404(self):
        """A non-connection Temporal error keeps the existing 404."""
        from temporalio.service import RPCError, RPCStatusCode

        from src.dependencies import get_temporal_client

        mock_client = Mock()
        mock_handle = Mock()

        async def missing_describe(*args, **kwargs):
            raise RPCError("workflow not found", RPCStatusCode.NOT_FOUND, b"")

        mock_handle.describe = missing_describe
        mock_client.get_workflow_handle.return_value = mock_handle

        async def mock_get_client():
            return mock_client

        app.dependency_overrides[get_temporal_client] = mock_get_client
        try:
            with TestClient(app) as client:
                response = client.get("/workflows/test-workflow-123/status")
                assert response.status_code == 404
        finally:
            app.dependency_overrides.pop(get_temporal_client, None)

    def test_approve_workflow_connection_failure_evicts_cache(self):
        """RPCError UNAVAILABLE from describe() evicts the cached client."""
        from temporalio.service import RPCError, RPCStatusCode

        import src.dependencies
        from src.dependencies import get_temporal_client

        mock_client = Mock()
        mock_handle = Mock()

        async def failing_describe(*args, **kwargs):
            raise RPCError("server unavailable", RPCStatusCode.UNAVAILABLE, b"")

        mock_handle.describe = failing_describe
        mock_client.get_workflow_handle.return_value = mock_handle

        async def mock_get_client():
            return mock_client

        src.dependencies._client_cache = mock_client
        app.dependency_overrides[get_temporal_client] = mock_get_client
        try:
            with TestClient(app) as client:
                response = client.post(
                    "/workflows/test-workflow-123/approve",
                    json={
                        "run_id": "run-123",
                        "approver_id": "jane.doe@example.com",
                        "content_digest": "digest-abc",
                    },
                )
                assert response.status_code == 503
                assert (
                    response.json()["detail"] == "Unable to connect to Temporal server"
                )
                # Checked before the client exits: app shutdown clears the cache.
                assert src.dependencies._client_cache is None
        finally:
            app.dependency_overrides.pop(get_temporal_client, None)

    def test_signal_workflow_connection_failure_evicts_cache(self):
        """RPCError UNAVAILABLE from describe() evicts the cached client."""
        from temporalio.service import RPCError, RPCStatusCode

        import src.dependencies
        from src.dependencies import get_temporal_client

        mock_client = Mock()
        mock_handle = Mock()

        async def failing_describe(*args, **kwargs):
            raise RPCError("server unavailable", RPCStatusCode.UNAVAILABLE, b"")

        mock_handle.describe = failing_describe
        mock_client.get_workflow_handle.return_value = mock_handle

        async def mock_get_client():
            return mock_client

        src.dependencies._client_cache = mock_client
        app.dependency_overrides[get_temporal_client] = mock_get_client
        try:
            with TestClient(app) as client:
                response = client.post(
                    "/workflows/test-workflow-123/signal",
                    json={
                        "signal_name": "provide_feedback",
                        "approver_id": "jane.doe@example.com",
                        "comments": "x",
                    },
                )
                assert response.status_code == 503
                assert (
                    response.json()["detail"] == "Unable to connect to Temporal server"
                )
                # Checked before the client exits: app shutdown clears the cache.
                assert src.dependencies._client_cache is None
        finally:
            app.dependency_overrides.pop(get_temporal_client, None)


class TestErrorDisclosure:
    """Regression: CWE-209 — backend exception text must not leak in HTTP responses."""

    @patch("src.routers.workflows.AVAILABLE_WORKFLOWS", {"TestWorkflow": Mock()})
    def test_exception_text_not_in_response_body(self):
        """Internal exception detail (e.g. backend host:port) must stay server-side."""
        sentinel = "dial tcp internal-temporal.prod.svc.cluster.local:7233"

        async def mock_get_client():
            mock_client = Mock()

            async def mock_start_workflow(*args, **kwargs):
                raise Exception(sentinel)

            mock_client.start_workflow = mock_start_workflow
            return mock_client

        from src.dependencies import get_temporal_client
        from src.main import app

        app.dependency_overrides[get_temporal_client] = mock_get_client

        try:
            with TestClient(app) as client:
                response = client.post(
                    "/workflows/start",
                    json={"workflow_type": "TestWorkflow", "input_data": {}},
                )

                assert response.status_code == 500
                # Generic message present, raw exception text absent
                assert "Failed to start workflow" in response.json()["detail"]
                assert sentinel not in response.text
        finally:
            app.dependency_overrides.clear()


class TestHealthTemporalTlsPosture:
    """Health routes connect through the shared Temporal client config (V-222596)."""

    @pytest.fixture(autouse=True)
    def _real_mode(self, monkeypatch, tls_pem_files):
        import src.config
        import src.dependencies

        monkeypatch.delenv("TESTING", raising=False)
        monkeypatch.delenv("INTEGRATION_TESTING", raising=False)
        monkeypatch.setenv("TEMPORAL_TLS_MODE", "mtls")
        monkeypatch.setenv("TEMPORAL_TLS_CERT", tls_pem_files["cert"])
        monkeypatch.setenv("TEMPORAL_TLS_KEY", tls_pem_files["key"])
        src.config._config = None
        src.dependencies._client_cache = None
        yield
        src.config._config = None
        src.dependencies._client_cache = None

    @staticmethod
    def _reachable_client():
        async def no_workflows(*args, **kwargs):
            for item in []:
                yield item

        mock_client = Mock()
        mock_client.list_workflows = no_workflows
        return mock_client

    def test_detailed_reports_temporal_healthy_with_configured_tls_mode(self):
        with (
            patch(
                "src.dependencies.create_temporal_client",
                AsyncMock(return_value=self._reachable_client()),
            ) as mock_create,
            TestClient(app) as client,
        ):
            response = client.get("/health/detailed")

        assert response.status_code == 200
        assert response.json()["dependencies"]["temporal"]["status"] == "healthy"
        mock_create.assert_awaited_once()
        assert mock_create.await_args.args[0].tls_mode == "mtls"

    def test_detailed_and_temporal_share_one_config_path(self):
        import src.dependencies

        configs = []

        async def record_and_connect(worker_config):
            configs.append(worker_config)
            return self._reachable_client()

        with (
            patch("src.dependencies.create_temporal_client", record_and_connect),
            TestClient(app) as client,
        ):
            assert client.get("/health/detailed").status_code == 200
            src.dependencies._client_cache = None
            assert client.get("/health/temporal").json()["status"] == "healthy"

        assert [c.tls_mode for c in configs] == ["mtls", "mtls"]

    def test_detailed_degrades_gracefully_on_connect_failure(self):
        with (
            patch(
                "src.dependencies.create_temporal_client",
                AsyncMock(side_effect=ConnectionError("secret-host:7233 refused")),
            ),
            TestClient(app) as client,
        ):
            response = client.get("/health/detailed")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "healthy"
        assert body["dependencies"]["temporal"]["status"] == "unhealthy"
        assert "secret-host" not in response.text

    def test_health_routes_reuse_one_cached_client(self):
        with (
            patch(
                "src.dependencies.create_temporal_client",
                AsyncMock(return_value=self._reachable_client()),
            ) as mock_create,
            TestClient(app) as client,
        ):
            for path in ("/health/detailed", "/health/temporal", "/health/ready"):
                assert client.get(path).json()["status"] == "healthy"

        mock_create.assert_awaited_once()

    def test_temporal_route_degrades_gracefully_on_connect_failure(self):
        with (
            patch(
                "src.dependencies.create_temporal_client",
                AsyncMock(side_effect=ConnectionError("secret-host:7233 refused")),
            ),
            TestClient(app) as client,
        ):
            response = client.get("/health/temporal")

        assert response.status_code == 200
        assert response.json()["status"] == "unhealthy"
        assert "secret-host" not in response.text

    @staticmethod
    def _assert_not_ready(response, *leaked: str) -> None:
        """Readiness failures share one sanitized 503 body."""
        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "unhealthy"
        assert body["service"] == "workflow-api"
        assert body["dependencies"] == {"temporal": "disconnected"}
        assert {"timestamp", "version"} <= body.keys()
        assert "detail" not in body
        for detail in leaked:
            assert detail not in response.text

    @staticmethod
    def _mock_logger() -> Mock:
        mock_logger = Mock()
        mock_logger.bind.return_value = mock_logger
        return mock_logger

    @staticmethod
    def _readiness_errors(mock_logger: Mock) -> list[str]:
        """Error lines logged by the readiness handler itself."""
        return [
            call.args[0]
            for call in mock_logger.error.call_args_list
            if call.args
            and isinstance(call.args[0], str)
            and call.args[0].startswith("Readiness check failed")
        ]

    def test_ready_reports_connected_when_probe_succeeds(self):
        probes = []

        async def record_probe(query, *args, **kwargs):
            probes.append(query)
            return
            yield  # pragma: no cover - makes this an async generator

        recording = Mock()
        recording.list_workflows = record_probe

        # Cold cache: readiness creates the shared client, then probes it.
        with (
            patch(
                "src.dependencies.create_temporal_client",
                AsyncMock(return_value=recording),
            ) as mock_create,
            TestClient(app) as client,
        ):
            response = client.get("/health/ready")

        mock_create.assert_awaited_once()
        assert len(probes) == 1
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "healthy"
        assert body["dependencies"] == {"temporal": "connected"}

    def test_ready_returns_503_when_initial_connect_fails(self, monkeypatch):
        # Configure the same namespace as the planted error, so a leak from either
        # the error text or the client config is caught.
        monkeypatch.setenv("TEMPORAL_NAMESPACE", "tenant-internal-ns")
        mock_logger = self._mock_logger()

        with (
            patch(
                "src.dependencies.create_temporal_client",
                AsyncMock(
                    side_effect=ConnectionError(
                        "secret-host:7233 namespace=tenant-internal-ns tls_mode=mtls refused"
                    )
                ),
            ) as mock_create,
            patch("foundry_temporal.logging.get_logger", return_value=mock_logger),
            TestClient(app) as client,
        ):
            response = client.get("/health/ready")

        mock_create.assert_awaited_once()
        self._assert_not_ready(response, "secret-host", "tenant-internal-ns", "mtls")
        errors = self._readiness_errors(mock_logger)
        assert len(errors) == 1
        assert "secret-host" in errors[0]
        assert errors[0].startswith("Readiness check failed: ConnectionError:")

    def test_ready_returns_503_and_logs_when_probe_fails(self, monkeypatch):
        import src.dependencies

        async def failing_rpc(*args, **kwargs):
            # Chained, so readiness must log this error rather than its cause.
            raise RuntimeError(
                "secret-host:7233 namespace=tenant-internal-ns tls_mode=mtls down"
            ) from OSError("transport reset")
            yield  # pragma: no cover - makes this an async generator

        broken = Mock()
        broken.list_workflows = failing_rpc
        monkeypatch.setattr(src.dependencies, "_client_cache", broken)
        mock_logger = self._mock_logger()

        with (
            patch("foundry_temporal.logging.get_logger", return_value=mock_logger),
            TestClient(app) as client,
        ):
            response = client.get("/health/ready")

        self._assert_not_ready(response, "secret-host", "tenant-internal-ns", "mtls")
        errors = self._readiness_errors(mock_logger)
        assert len(errors) == 1
        assert "secret-host" in errors[0]
        assert errors[0].startswith("Readiness check failed: RuntimeError:")
        assert "transport reset" not in errors[0]

    @pytest.mark.parametrize(
        ("path", "log_prefix"),
        [
            ("/health/detailed", "Detailed health check - Temporal connection failed"),
            ("/health/temporal", "Temporal health check failed"),
            ("/health/ready", "Readiness check failed"),
        ],
    )
    def test_health_routes_log_the_connection_error(self, path, log_prefix):
        mock_logger = self._mock_logger()

        with (
            patch(
                "src.dependencies.create_temporal_client",
                AsyncMock(side_effect=ConnectionError("secret-host:7233 refused")),
            ),
            patch("foundry_temporal.logging.get_logger", return_value=mock_logger),
            TestClient(app) as client,
        ):
            response = client.get(path)

        assert "secret-host" not in response.text
        errors = [
            call.args[0]
            for call in mock_logger.error.call_args_list
            if call.args
            and isinstance(call.args[0], str)
            and call.args[0].startswith(log_prefix)
        ]
        assert errors == [f"{log_prefix}: ConnectionError: secret-host:7233 refused"]

    @pytest.mark.parametrize(
        ("path", "query"),
        [
            ("/health/detailed", "WorkflowId = '__connectivity_check__'"),
            ("/health/temporal", "WorkflowId = '__temporal_health_check__'"),
            ("/health/ready", "WorkflowId = '__connectivity_check__'"),
        ],
    )
    def test_health_routes_send_fixed_queries(self, monkeypatch, path, query):
        import src.dependencies

        queries = []

        async def record_query(sent, *args, **kwargs):
            queries.append(sent)
            yield Mock()  # one match, so the probe takes its early break
            raise AssertionError("the probe should stop after one result")

        recording = Mock()
        recording.list_workflows = record_query
        monkeypatch.setattr(src.dependencies, "_client_cache", recording)

        with TestClient(app) as client:
            assert client.get(path).status_code == 200

        assert queries == [query]

    def test_workflow_route_keeps_503_on_connect_failure(self):
        with (
            patch(
                "src.dependencies.create_temporal_client",
                AsyncMock(side_effect=ConnectionError("secret-host:7233 refused")),
            ),
            TestClient(app) as client,
        ):
            response = client.get("/workflows/")

        assert response.status_code == 503
        assert response.json() == {"detail": "Unable to connect to Temporal server"}

    @staticmethod
    def _failing_client(error: Exception) -> Mock:
        async def failing_rpc(*args, **kwargs):
            raise error
            yield  # pragma: no cover - makes this an async generator

        client = Mock()
        client.list_workflows = failing_rpc
        return client

    @pytest.mark.parametrize(
        "status_code", [RPCStatusCode.PERMISSION_DENIED, RPCStatusCode.INVALID_ARGUMENT]
    )
    def test_ready_stays_ready_when_temporal_rejects_the_probe(
        self, monkeypatch, status_code
    ):
        import src.dependencies

        client = self._failing_client(RPCError("secret-host denied", status_code, b""))
        monkeypatch.setattr(src.dependencies, "_client_cache", client)
        mock_logger = self._mock_logger()

        with (
            patch("foundry_temporal.logging.get_logger", return_value=mock_logger),
            TestClient(app) as test_client,
        ):
            response = test_client.get("/health/ready")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "healthy"
        assert body["dependencies"] == {"temporal": "degraded"}
        assert "secret-host" not in response.text
        warnings = [
            call.args[0]
            for call in mock_logger.warning.call_args_list
            if call.args and str(call.args[0]).startswith("Readiness check failed")
        ]
        assert warnings == ["Readiness check failed: RPCError: secret-host denied"]
        assert self._readiness_errors(mock_logger) == []

    @pytest.mark.parametrize(
        "status_code",
        [
            RPCStatusCode.UNAVAILABLE,
            RPCStatusCode.UNAUTHENTICATED,
            RPCStatusCode.NOT_FOUND,
            RPCStatusCode.FAILED_PRECONDITION,
            RPCStatusCode.DEADLINE_EXCEEDED,
            RPCStatusCode.UNKNOWN,
            RPCStatusCode.UNIMPLEMENTED,
        ],
    )
    def test_ready_returns_503_on_connection_failure_status(
        self, monkeypatch, status_code
    ):
        import src.dependencies

        client = self._failing_client(RPCError("secret-host gone", status_code, b""))
        monkeypatch.setattr(src.dependencies, "_client_cache", client)

        with TestClient(app) as test_client:
            response = test_client.get("/health/ready")

        self._assert_not_ready(response, "secret-host")

    def test_ready_returns_503_when_connect_fails_with_an_rpc_error(self):
        denied = RPCError("secret-host denied", RPCStatusCode.PERMISSION_DENIED, b"")

        with (
            patch(
                "src.dependencies.create_temporal_client",
                AsyncMock(side_effect=RuntimeError("connect failed")),
            ) as mock_create,
            TestClient(app) as test_client,
        ):
            mock_create.side_effect.__cause__ = denied
            response = test_client.get("/health/ready")

        self._assert_not_ready(response, "secret-host")

    @pytest.fixture
    def gated_connect(self, monkeypatch):
        """A create_temporal_client that blocks until released, freed at teardown.

        The app runs on TestClient's own loop thread, so a threading.Event (waited
        on in a worker thread) gates it; teardown releases it so no fake connect
        is left pending when the loop shuts down.
        """
        import src.dependencies

        monkeypatch.setattr(src.dependencies, "_connect_task", None)
        monkeypatch.setattr(src.dependencies, "_connect_waiters", 0)
        monkeypatch.setenv("TEMPORAL_PROBE_TIMEOUT_SECONDS", "0.05")
        release = threading.Event()
        reachable = self._reachable_client()

        async def blocked(*_args, **_kwargs):
            await asyncio.to_thread(release.wait)
            return reachable

        connect = AsyncMock(side_effect=blocked)
        try:
            yield connect, release
        finally:
            release.set()

    @pytest.mark.parametrize(
        ("path", "expected_status", "log_prefix"),
        [
            ("/health/ready", 503, "Readiness check failed"),
            (
                "/health/detailed",
                200,
                "Detailed health check - Temporal connection failed",
            ),
            ("/health/temporal", 200, "Temporal health check failed"),
        ],
    )
    def test_hung_cold_connect_is_bounded(
        self, gated_connect, path, expected_status, log_prefix
    ):
        connect, release = gated_connect
        mock_logger = self._mock_logger()

        with (
            patch("src.dependencies.create_temporal_client", connect),
            patch("foundry_temporal.logging.get_logger", return_value=mock_logger),
            TestClient(app) as client,
        ):
            try:
                response = client.get(path)
            finally:
                release.set()

        assert response.status_code == expected_status
        if path == "/health/ready":
            assert response.json()["dependencies"] == {"temporal": "disconnected"}
        errors = [
            call.args[0]
            for call in mock_logger.error.call_args_list
            if call.args
            and isinstance(call.args[0], str)
            and call.args[0].startswith(log_prefix)
        ]
        assert errors == [
            f"{log_prefix}: TemporalConnectTimeoutError: "
            "Temporal connect timed out after 0.05s"
        ]

    def test_connect_limit_is_logged_as_the_limit_not_the_budget(
        self, gated_connect, monkeypatch
    ):
        """When the shared connect hits TEMPORAL_CONNECT_TIMEOUT_SECONDS inside the
        health budget, the log names that limit, not the health check budget."""
        connect, release = gated_connect
        monkeypatch.setenv("TEMPORAL_PROBE_TIMEOUT_SECONDS", "1.0")
        monkeypatch.setenv("TEMPORAL_CONNECT_TIMEOUT_SECONDS", "0.05")
        mock_logger = self._mock_logger()

        with (
            patch("src.dependencies.create_temporal_client", connect),
            patch("foundry_temporal.logging.get_logger", return_value=mock_logger),
            TestClient(app) as client,
        ):
            try:
                response = client.get("/health/ready")
            finally:
                release.set()

        assert response.status_code == 503
        assert self._readiness_errors(mock_logger) == [
            "Readiness check failed: TemporalConnectTimeoutError: "
            "Temporal connect timed out after 0.05s (TEMPORAL_CONNECT_TIMEOUT_SECONDS)"
        ]

    def test_workflow_route_answers_503_at_the_connect_limit(
        self, gated_connect, monkeypatch
    ):
        """A /workflows/* request during a hung connect answers 503 once the
        connect limit passes, instead of waiting forever."""
        connect, release = gated_connect
        monkeypatch.setenv("TEMPORAL_CONNECT_TIMEOUT_SECONDS", "0.05")

        with (
            patch("src.dependencies.create_temporal_client", connect),
            TestClient(app) as client,
        ):
            try:
                response = client.get("/workflows/test-workflow-123/status")
            finally:
                release.set()

        assert response.status_code == 503
        assert response.json()["detail"] == "Unable to connect to Temporal server"

    def test_cancelled_shared_connect_answers_503_disconnected(self):
        from src.dependencies import TemporalConnectCancelledError

        with (
            patch(
                "src.routers.health.acquire_temporal_client",
                side_effect=TemporalConnectCancelledError,
            ),
            TestClient(app) as client,
        ):
            response = client.get("/health/ready")

        assert response.status_code == 503
        assert response.json()["dependencies"] == {"temporal": "disconnected"}

    def test_slow_connect_completes_across_readiness_checks(self, gated_connect):
        connect, release = gated_connect

        with (
            patch("src.dependencies.create_temporal_client", connect),
            TestClient(app) as client,
        ):
            first = client.get("/health/ready")
            release.set()
            later = first
            for _ in range(40):
                later = client.get("/health/ready")
                if later.status_code == 200:
                    break

        assert first.status_code == 503
        assert later.status_code == 200
        assert later.json()["dependencies"] == {"temporal": "connected"}
        connect.assert_awaited_once()

    def test_probe_gets_only_the_time_left_after_the_connect(self, monkeypatch):
        import time

        import src.dependencies

        monkeypatch.setattr(src.dependencies, "_connect_task", None)
        monkeypatch.setattr(src.dependencies, "_connect_waiters", 0)
        monkeypatch.setenv("TEMPORAL_PROBE_TIMEOUT_SECONDS", "1.0")
        release = threading.Event()

        async def hung_rpc(*args, **kwargs):
            await asyncio.to_thread(release.wait)
            return
            yield  # pragma: no cover - makes this an async generator

        hung = Mock()
        hung.list_workflows = hung_rpc

        async def slow(*_args, **_kwargs):
            await asyncio.sleep(0.6)
            return hung

        mock_logger = self._mock_logger()
        with (
            patch(
                "src.dependencies.create_temporal_client", AsyncMock(side_effect=slow)
            ),
            patch("foundry_temporal.logging.get_logger", return_value=mock_logger),
            TestClient(app) as client,
        ):
            try:
                started = time.monotonic()
                response = client.get("/health/ready")
                elapsed = time.monotonic() - started
            finally:
                release.set()

        assert response.status_code == 503
        # With the full 1.0 s probe after a 0.6 s connect it would take about 1.6 s.
        assert elapsed < 1.4
        errors = self._readiness_errors(mock_logger)
        assert len(errors) == 1
        # The log names the time the probe really had, not the full budget.
        assert re.fullmatch(
            r"Readiness check failed: TemporalProbeTimeoutError: Temporal probe "
            r"timed out after 0\.\d+s \(0\.\d+s of the 1s budget went to connecting\)",
            errors[0],
        )

    def test_inner_timeout_is_not_reported_as_the_probe_timeout(self, monkeypatch):
        import src.dependencies

        client = self._failing_client(TimeoutError("secret-host inner timeout"))
        monkeypatch.setattr(src.dependencies, "_client_cache", client)
        mock_logger = self._mock_logger()

        with (
            patch("foundry_temporal.logging.get_logger", return_value=mock_logger),
            TestClient(app) as test_client,
        ):
            response = test_client.get("/health/ready")

        self._assert_not_ready(response, "secret-host")
        errors = self._readiness_errors(mock_logger)
        assert errors == [
            "Readiness check failed: TimeoutError: secret-host inner timeout"
        ]

    def test_ready_returns_503_on_non_rpc_probe_error(self, monkeypatch):
        import src.dependencies

        client = self._failing_client(ValueError("secret-host unexpected"))
        monkeypatch.setattr(src.dependencies, "_client_cache", client)

        with TestClient(app) as test_client:
            response = test_client.get("/health/ready")

        self._assert_not_ready(response, "secret-host")

    @pytest.mark.parametrize(
        ("path", "log_prefix"),
        [
            ("/health/detailed", "Detailed health check - Temporal connection failed"),
            ("/health/temporal", "Temporal health check failed"),
            ("/health/ready", "Readiness check failed"),
        ],
    )
    def test_probe_timeout_is_logged_as_a_timeout(self, monkeypatch, path, log_prefix):
        import src.dependencies

        async def hung_rpc(*args, **kwargs):
            await asyncio.sleep(30)
            yield  # pragma: no cover - makes this an async generator

        hung = Mock()
        hung.list_workflows = hung_rpc
        monkeypatch.setattr(src.dependencies, "_client_cache", hung)
        monkeypatch.setenv("TEMPORAL_PROBE_TIMEOUT_SECONDS", "0.05")
        mock_logger = self._mock_logger()

        with (
            patch("foundry_temporal.logging.get_logger", return_value=mock_logger),
            TestClient(app) as test_client,
        ):
            test_client.get(path)

        errors = [
            call.args[0]
            for call in mock_logger.error.call_args_list
            if call.args and str(call.args[0]).startswith(log_prefix)
        ]
        assert errors == [
            f"{log_prefix}: TemporalProbeTimeoutError: "
            "Temporal probe timed out after 0.05s"
        ]

    def test_repeated_probe_timeouts_discard_the_client(self, monkeypatch):
        """At the threshold, readiness discards the hung client, and the next
        check connects again and passes."""
        import src.dependencies

        async def hung_rpc(*args, **kwargs):
            await asyncio.sleep(30)
            yield  # pragma: no cover - makes this an async generator

        hung = Mock()
        hung.list_workflows = hung_rpc
        monkeypatch.setattr(src.dependencies, "_client_cache", hung)
        monkeypatch.setenv("TEMPORAL_PROBE_TIMEOUT_SECONDS", "0.05")
        monkeypatch.setenv("TEMPORAL_PROBE_TIMEOUTS_BEFORE_RECONNECT", "2")

        with (
            patch(
                "src.dependencies.create_temporal_client",
                AsyncMock(return_value=self._reachable_client()),
            ) as mock_create,
            TestClient(app) as test_client,
        ):
            assert test_client.get("/health/ready").status_code == 503
            assert src.dependencies._client_cache is hung
            assert test_client.get("/health/ready").status_code == 503
            assert src.dependencies._client_cache is None

            response = test_client.get("/health/ready")

        assert response.status_code == 200
        assert response.json()["dependencies"]["temporal"] == "connected"
        mock_create.assert_awaited_once()

    @pytest.mark.parametrize("path", ["/health/detailed", "/health/temporal"])
    def test_diagnostic_probe_timeouts_do_not_count(self, monkeypatch, path):
        """Only readiness counts probe timeouts, so diagnostic checks keep the client."""
        import src.dependencies

        async def hung_rpc(*args, **kwargs):
            await asyncio.sleep(30)
            yield  # pragma: no cover - makes this an async generator

        hung = Mock()
        hung.list_workflows = hung_rpc
        monkeypatch.setattr(src.dependencies, "_client_cache", hung)
        monkeypatch.setenv("TEMPORAL_PROBE_TIMEOUT_SECONDS", "0.05")
        monkeypatch.setenv("TEMPORAL_PROBE_TIMEOUTS_BEFORE_RECONNECT", "1")

        with TestClient(app) as test_client:
            response = test_client.get(path)
            assert src.dependencies._client_cache is hung

        assert response.status_code == 200
        assert src.dependencies._probe_timeouts == 0

    def test_connect_timeouts_do_not_count(self, monkeypatch):
        """A check that times out waiting for the client counts nothing."""
        never = asyncio.Event()

        async def hung_connect(*_args, **_kwargs):
            await never.wait()

        monkeypatch.setenv("TEMPORAL_PROBE_TIMEOUT_SECONDS", "0.05")
        monkeypatch.setenv("TEMPORAL_PROBE_TIMEOUTS_BEFORE_RECONNECT", "1")

        with (
            patch("src.dependencies.create_temporal_client", side_effect=hung_connect),
            patch("src.routers.health.record_probe_timeout") as mock_record,
            TestClient(app) as test_client,
        ):
            assert test_client.get("/health/ready").status_code == 503

        mock_record.assert_not_called()

    def test_inner_timeout_error_does_not_count(self, monkeypatch):
        """A TimeoutError from inside the probe, before its deadline, keeps the client."""
        import src.dependencies

        async def inner_timeout(*args, **kwargs):
            raise TimeoutError("inner")
            yield  # pragma: no cover - makes this an async generator

        client = Mock()
        client.list_workflows = inner_timeout
        monkeypatch.setattr(src.dependencies, "_client_cache", client)
        monkeypatch.setenv("TEMPORAL_PROBE_TIMEOUTS_BEFORE_RECONNECT", "1")

        with TestClient(app) as test_client:
            assert test_client.get("/health/ready").status_code == 503
            assert src.dependencies._client_cache is client

    def test_successful_probe_resets_the_count(self, monkeypatch):
        """A probe that answers clears the timeouts counted on its client."""
        import src.dependencies

        client = self._reachable_client()
        monkeypatch.setattr(src.dependencies, "_client_cache", client)
        monkeypatch.setattr(src.dependencies, "_probe_timeout_client", client)
        monkeypatch.setattr(src.dependencies, "_probe_timeouts", 2)

        with TestClient(app) as test_client:
            assert test_client.get("/health/ready").status_code == 200

        assert src.dependencies._probe_timeouts == 0


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (RPCError("denied", RPCStatusCode.PERMISSION_DENIED, b""), "degraded"),
        (RPCError("bad", RPCStatusCode.INVALID_ARGUMENT, b""), "degraded"),
        (RPCError("no namespace", RPCStatusCode.NOT_FOUND, b""), "disconnected"),
        (RPCError("standby", RPCStatusCode.FAILED_PRECONDITION, b""), "disconnected"),
        (RPCError("quota", RPCStatusCode.RESOURCE_EXHAUSTED, b""), "disconnected"),
        (RPCError("gone", RPCStatusCode.UNAVAILABLE, b""), "disconnected"),
        (RPCError("auth", RPCStatusCode.UNAUTHENTICATED, b""), "disconnected"),
        (RPCError("late", RPCStatusCode.DEADLINE_EXCEEDED, b""), "disconnected"),
        (RPCError("stop", RPCStatusCode.CANCELLED, b""), "disconnected"),
        (RPCError("odd", RPCStatusCode.UNKNOWN, b""), "disconnected"),
        (RPCError("wrong", RPCStatusCode.UNIMPLEMENTED, b""), "disconnected"),
        (ConnectionError("refused"), "disconnected"),
    ],
)
def test_probe_failure_state_fails_closed(error, expected):
    from src.routers.health import TemporalProbeTimeoutError, _probe_failure_state

    assert _probe_failure_state(error) == expected
    assert _probe_failure_state(TemporalProbeTimeoutError(0.9)) == "disconnected"


@pytest.mark.parametrize(
    ("temporal", "expected_status", "expected_body_status"),
    [
        # None mirrors FastAPI's injected sub-response: ready states leave it unset.
        ("connected", None, "healthy"),
        ("test_mode", None, "healthy"),
        ("degraded", None, "healthy"),
        ("disconnected", 503, "unhealthy"),
    ],
)
def test_readiness_response_status_by_state(
    temporal, expected_status, expected_body_status
):
    from fastapi import Response

    from src.routers.health import _readiness_response

    response = Response()
    response.status_code = None  # type: ignore[assignment]
    body = _readiness_response(response, temporal)

    assert response.status_code == expected_status
    assert body.status == expected_body_status
    assert body.dependencies == {"temporal": temporal}


def test_ready_openapi_declares_503():
    responses = app.openapi()["paths"]["/health/ready"]["get"]["responses"]
    assert {"200", "503"} <= responses.keys()
    schema = responses["503"]["content"]["application/json"]["schema"]
    assert schema["$ref"].endswith("/HealthCheckResponse")


def test_worker_config_built_only_in_shared_helper():
    """Every Temporal connection gets its config, and tls_mode, from one place."""
    import ast
    from pathlib import Path

    # (module, name) pairs whose calls build a Temporal client config or connection.
    guarded = {
        ("foundry_temporal.worker", "WorkerConfig"),
        ("foundry_temporal.worker", "create_temporal_client"),
        ("temporalio.client", "Client"),
    }

    def bound_names(tree):
        """Local name -> guarded name, following import aliases."""
        return {
            alias.asname or alias.name: alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
            if (node.module, alias.name) in guarded
        }

    def guarded_call(node, names):
        """WorkerConfig(...), WorkerConfig.from_env(), create_temporal_client(...),
        Client.connect(...) or getattr(Client, "connect"), however imported."""
        if not isinstance(node, ast.Call):
            return None
        func = node.func
        if isinstance(func, ast.Name) and func.id == "getattr" and len(node.args) >= 2:
            target, attr = node.args[0], getattr(node.args[1], "value", None)
            if attr == "connect" and (
                (isinstance(target, ast.Name) and names.get(target.id) == "Client")
                or (isinstance(target, ast.Attribute) and target.attr == "Client")
            ):
                return "Client.connect"
            return None
        if isinstance(func, ast.Name):
            name = names.get(func.id)
            return name if name in ("WorkerConfig", "create_temporal_client") else None
        if isinstance(func, ast.Attribute):
            owner = func.value
            owner_name = (
                names.get(owner.id)
                if isinstance(owner, ast.Name)
                else owner.attr
                if isinstance(owner, ast.Attribute)
                else None
            )
            if owner_name == "Client" and func.attr == "connect":
                return "Client.connect"
            if owner_name == "WorkerConfig":
                return "WorkerConfig"
            if func.attr in ("WorkerConfig", "create_temporal_client"):
                return func.attr
        return None

    src_dir = Path(__file__).resolve().parents[2] / "src"
    uses = set()
    for path in src_dir.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = bound_names(tree)
        parents = {
            child: node
            for node in ast.walk(tree)
            for child in ast.iter_child_nodes(node)
        }
        for node in ast.walk(tree):
            name = guarded_call(node, names)
            if name is None:
                continue
            scope = node
            while scope in parents and not isinstance(
                scope, ast.FunctionDef | ast.AsyncFunctionDef
            ):
                scope = parents[scope]
            func = getattr(scope, "name", "<module>")
            uses.add((path.relative_to(src_dir).as_posix(), func, name))

    # The config is built only in the shared builder and connected only through
    # the single-flight connect task behind get_temporal_client, never via a raw
    # Client.connect, so no route can open its own connection. Type annotations
    # and imports are not uses.
    assert uses == {
        ("dependencies.py", "build_temporal_client_config", "WorkerConfig"),
        ("dependencies.py", "_connect", "create_temporal_client"),
    }


@pytest.mark.parametrize(
    ("probe_seconds", "budget_seconds", "message"),
    [
        (0.9, 0.9, "Temporal probe timed out after 0.9s"),
        (0.8999, 0.9, "Temporal probe timed out after 0.9s"),
        (
            0.4,
            1.0,
            "Temporal probe timed out after 0.4s "
            "(0.6s of the 1s budget went to connecting)",
        ),
    ],
)
def test_probe_timeout_message_names_the_time_the_probe_had(
    probe_seconds, budget_seconds, message
):
    from src.routers.health import TemporalProbeTimeoutError

    assert str(TemporalProbeTimeoutError(probe_seconds, budget_seconds)) == message


@pytest.mark.parametrize(
    ("probe_seconds", "budget_seconds", "message"),
    [
        (
            12.344,
            300.0,
            "Temporal probe timed out after 12.34s "
            "(287.66s of the 300s budget went to connecting)",
        ),
        (
            150.0,
            300.0,
            "Temporal probe timed out after 150s "
            "(150s of the 300s budget went to connecting)",
        ),
    ],
)
def test_probe_timeout_message_keeps_large_values_readable(
    probe_seconds, budget_seconds, message
):
    from src.routers.health import TemporalProbeTimeoutError

    assert str(TemporalProbeTimeoutError(probe_seconds, budget_seconds)) == message
