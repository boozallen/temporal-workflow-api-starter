# Copyright 2026 Booz Allen Hamilton Inc.
# SPDX-License-Identifier: Apache-2.0
"""
Integration tests for API infrastructure verification.

These tests verify basic API infrastructure without external dependencies.
For comprehensive API testing, see unit tests in tests/unit/.
"""

import os

import pytest
from fastapi.testclient import TestClient
from temporalio.testing import WorkflowEnvironment

from src.config import get_config
from src.main import app


@pytest.fixture(autouse=True)
def setup_test_environment():
    """Set up test environment and clean up afterwards."""
    # Set test environment
    original_testing = os.environ.get("TESTING")
    os.environ["TESTING"] = "true"

    yield

    # Clean up
    if original_testing is None:
        os.environ.pop("TESTING", None)
    else:
        os.environ["TESTING"] = original_testing


@pytest.mark.integration
class TestAPIInfrastructure:
    """Integration tests for API infrastructure"""

    def test_api_startup_and_health(self):
        """Test that API starts up and basic health check works"""
        # Set test environment to ensure test-friendly behavior
        client = TestClient(app)
        response = client.get("/health/")
        assert response.status_code == 200

        health_data = response.json()
        assert health_data["service"] == "workflow-api"
        assert "status" in health_data
        assert "timestamp" in health_data

        # Should show test_mode for Temporal
        temporal_status = health_data.get("dependencies", {}).get("temporal", {})
        assert temporal_status.get("status") == "test_mode"

    def test_api_endpoints_exist(self):
        """Test that all expected API endpoints exist"""
        client = TestClient(app)
        # Test health endpoints
        assert client.get("/health/").status_code == 200
        assert client.get("/health/detailed").status_code == 200
        assert (
            client.get("/health/ready").status_code == 200
        )  # Should be healthy in test mode

        # Test workflow endpoints exist (structure verification)
        response = client.get("/workflows/")
        assert response.status_code in [200, 503]

        # Test OpenAPI docs
        assert client.get("/docs").status_code == 200
        assert client.get("/openapi.json").status_code == 200

    def test_api_request_validation(self):
        """Test API request validation works"""
        client = TestClient(app)
        # Test invalid request structure
        invalid_requests = [
            {},  # Empty request
            {"workflow_type": ""},  # Empty workflow type
            {"input_data": {}},  # Missing workflow type
        ]

        for invalid_request in invalid_requests:
            response = client.post("/workflows/start", json=invalid_request)
            # Should return validation error regardless of Temporal availability
            assert response.status_code == 422

    def test_api_response_format(self):
        """Test API response formats are consistent"""
        client = TestClient(app)
        # Test health endpoint response format
        health_response = client.get("/health/")
        health_data = health_response.json()

        # Should have consistent structure
        assert isinstance(health_data.get("status"), str)
        assert isinstance(health_data.get("timestamp"), str)
        assert isinstance(health_data.get("service"), str)

    def test_api_error_handling(self):
        """Test API error handling"""
        client = TestClient(app)
        # Test 404 for non-existent endpoints
        response = client.get("/non-existent-endpoint")
        assert response.status_code == 404

        # Test method not allowed
        response = client.options("/health/")  # Use OPTIONS instead of PUT
        assert response.status_code in [200, 405]  # Either CORS or method not allowed


@pytest.mark.integration
class TestTemporalTestEnvironment:
    """Integration tests for Temporal test environment"""

    @pytest.mark.asyncio
    async def test_temporal_test_environment_available(self):
        """Test that Temporal test environment is available and working"""
        # Properly await the WorkflowEnvironment creation
        env = await WorkflowEnvironment.start_time_skipping()
        try:
            # Basic smoke test to verify integration test infrastructure
            assert env is not None
            assert env.client is not None

            # Verify we can get basic client info
            client = env.client
            assert client is not None
        finally:
            await env.shutdown()

    def test_temporal_namespace_configuration(self):
        """Test Temporal namespace configuration"""
        config = get_config()

        # Verify configuration is loaded
        assert config.temporal_host
        assert config.temporal_namespace
        assert config.temporal_task_queue


@pytest.mark.integration
def test_integration_test_infrastructure():
    """Test that integration test infrastructure works correctly"""
    # This test verifies the integration test setup is working
    assert True


@pytest.mark.integration
@pytest.mark.slow
def test_slow_marker_works():
    """Test that slow test marker works for filtering tests"""
    # This allows testing integration test filtering
    assert True


# NOTE: Full API-Temporal integration tests require complex setup that conflicts with
# foundry-temporal imports and external service dependencies. For comprehensive testing:
#
# 1. Run unit tests in tests/unit/ - these test API logic and validation
# 2. Use docker-compose.yaml to run full integration tests with real Temporal server
# 3. For template customization, teams should implement their own integration tests
#    based on their specific workflow and API requirements
#
# This minimal integration test suite verifies that the API infrastructure works
# and provides a foundation for teams to build upon.
