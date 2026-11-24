# Copyright 2026 Booz Allen Hamilton Inc.
# SPDX-License-Identifier: Apache-2.0
"""
Integration tests for Temporal Workflow API

These tests require external dependencies and test the complete system:
- FastAPI application
- Temporal server connectivity
- Workflow lifecycle through API endpoints
- End-to-end request/response flows

Run with: pytest tests/integration/ -m integration
"""
