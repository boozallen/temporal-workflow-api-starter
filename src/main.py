# Copyright 2026 Booz Allen Hamilton Inc.
# SPDX-License-Identifier: Apache-2.0
"""
TEMPLATE: FastAPI application for Temporal workflows with foundry-temporal integration

Customize this application for your specific workflow requirements.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from .config import (
    AppConfig,
    assert_mock_mode_safe,
    assert_tls_mode_safe,
    get_config,
    warn_if_tls_paths_ignored,
)
from .dependencies import (
    allow_temporal_connects,
    close_cached_temporal_client,
    require_api_key,
)
from .middleware import RequestBodyLimitMiddleware
from .routers import health, workflows
from .version import APP_VERSION

# foundry-temporal imports are deferred until runtime when packages are available


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Application lifespan events with foundry-temporal logging."""
    # Setup foundry structured logging (REQUIRED)
    from foundry_temporal.logging import get_logger

    logger = get_logger()
    logger.info(
        "Starting Workflow API with foundry-temporal integration",
        version=app.version,
        foundry_temporal=True,
    )

    # Startup
    allow_temporal_connects()
    try:
        yield
    finally:
        # Shutdown runs even when the lifespan is cancelled or an error is raised
        # at yield, so the cached client's token-refresh task is always cancelled.
        logger.info("Shutting down Workflow API", version=app.version)
        await close_cached_temporal_client()


def create_app() -> FastAPI:
    """TEMPLATE: Create FastAPI application with foundry-temporal integration."""

    config = get_config()

    # Refuse to start with mock mode or a plaintext Temporal connection in production
    assert_mock_mode_safe(config)
    assert_tls_mode_safe(config)

    # Setup foundry structured logging early (REQUIRED)
    from foundry_temporal.logging import setup_structured_logging

    setup_structured_logging(config.log_level)
    warn_if_tls_paths_ignored(config)

    app = FastAPI(
        title="Workflow API",
        description="REST API for managing Temporal workflows with foundry-temporal",
        version=APP_VERSION,
        lifespan=lifespan,
    )

    # Add foundry-temporal error handlers (REQUIRED)
    add_foundry_error_handlers(app)

    # Resolve X-Forwarded-For from trusted proxies only, so request.client.host
    # (used for per-client auth-failure throttling) reflects the real caller
    # instead of the proxy in front of the app.
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=config.trusted_proxy_hosts)

    # Added last so it runs first among added middleware: oversized bodies get
    # 413 before other added middleware, auth, or validation touches them.
    app.add_middleware(
        RequestBodyLimitMiddleware, max_body_bytes=config.max_request_body_bytes
    )

    # Include routers
    app.include_router(health.router, prefix="/health", tags=["Health"])
    app.include_router(
        workflows.router,
        prefix="/workflows",
        tags=["Workflows"],
        dependencies=[Depends(require_api_key)],
    )

    return app


def add_foundry_error_handlers(app: FastAPI) -> None:
    """Add error handlers for foundry-temporal error types."""
    # Import foundry error types dynamically
    from foundry_temporal.errors import (
        ConfigurationError,
        ValidationError,
        WorkflowError,
    )

    @app.exception_handler(WorkflowError)
    async def workflow_error_handler(
        request: Request, exc: WorkflowError
    ) -> JSONResponse:
        """Handle foundry workflow errors."""
        from .models import ErrorResponse

        return JSONResponse(
            status_code=400,
            content=ErrorResponse(
                error="Workflow operation failed",
                error_type="WorkflowError",
                error_code="WORKFLOW_ERROR",
                retryable=not exc.non_retryable,
                detail=None,
            ).model_dump(mode="json"),
        )

    @app.exception_handler(ValidationError)
    async def validation_error_handler(
        request: Request, exc: ValidationError
    ) -> JSONResponse:
        """Handle foundry validation errors."""
        from .models import ErrorResponse

        return JSONResponse(
            status_code=422,
            content=ErrorResponse(
                error="Request validation failed",
                error_type="ValidationError",
                error_code="VALIDATION_ERROR",
                retryable=False,
                detail=None,
            ).model_dump(mode="json"),
        )

    @app.exception_handler(ConfigurationError)
    async def configuration_error_handler(
        request: Request, exc: ConfigurationError
    ) -> JSONResponse:
        """Handle foundry configuration errors."""
        from .models import ErrorResponse

        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                error="Server configuration error",
                error_type="ConfigurationError",
                error_code="CONFIG_ERROR",
                retryable=False,
                detail=None,
            ).model_dump(mode="json"),
        )


# Logging is configured by foundry-temporal setup_structured_logging() in create_app()

# Create application instance
app = create_app()


if __name__ == "__main__":
    import uvicorn

    config = AppConfig()
    uvicorn.run("src.main:app", host=config.api_host, port=config.api_port, reload=True)
