# Copyright 2026 Booz Allen Hamilton Inc.
# SPDX-License-Identifier: Apache-2.0
"""
TEMPLATE: API package

FastAPI application components for Temporal workflow API gateway.
"""

from .config import get_config
from .models import ErrorResponse, WorkflowRequest, WorkflowResponse

# Note: dependencies imports are deferred to avoid foundry_temporal import issues
# Import get_temporal_client and get_workflow_config from .dependencies when needed

__all__ = [
    "get_config",
    "WorkflowRequest",
    "WorkflowResponse",
    "ErrorResponse",
]
