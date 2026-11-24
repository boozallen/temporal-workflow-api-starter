# Copyright 2026 Booz Allen Hamilton Inc.
# SPDX-License-Identifier: Apache-2.0
"""
Smoke tests to catch boot failures before deployment.

These tests verify that the application can start without import errors,
syntax errors, or other module-level failures that would prevent uvicorn
from serving requests.
"""

import pytest


class TestApplicationBoot:
    """Test that the application can boot successfully"""

    def test_main_module_imports(self):
        """Test that src.main can be imported without errors.

        This catches TYPE_CHECKING-related NameErrors and other module-level
        failures that would prevent the app from starting.
        """
        try:
            import src.main

            assert src.main is not None
        except ImportError as e:
            # Allow ImportError for missing dependencies in minimal test environments
            if (
                "pydantic" in str(e)
                or "fastapi" in str(e)
                or "foundry_temporal" in str(e)
            ):
                pytest.skip(f"Skipping due to missing dependency: {e}")
            raise

    def test_app_can_be_instantiated(self):
        """Test that the FastAPI app instance can be created.

        This verifies the app object exists and is properly configured,
        catching errors in create_app() that would prevent serving.
        """
        try:
            from src.main import app

            assert app is not None
            assert app.title == "Workflow API"
        except ImportError as e:
            if (
                "pydantic" in str(e)
                or "fastapi" in str(e)
                or "foundry_temporal" in str(e)
            ):
                pytest.skip(f"Skipping due to missing dependency: {e}")
            raise
