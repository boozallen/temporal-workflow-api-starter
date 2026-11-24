# Copyright 2026 Booz Allen Hamilton Inc.
# SPDX-License-Identifier: Apache-2.0
"""
TEMPLATE: Configuration management for Temporal workflow API

This template provides essential configuration for Temporal workflow APIs with
foundry-temporal.
Teams can customize this configuration class to add their specific settings.
"""

import os
from pathlib import Path
from typing import Literal, Self

from foundry_agent_core import _MAX_DICT_BYTES
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_API_AUTH_TOKEN_LENGTH = 32

# Under Kubernetes' default 1 second probe timeout, so a health probe gets a
# response instead of timing out while temporalio retries the RPC.
DEFAULT_TEMPORAL_PROBE_TIMEOUT_SECONDS = 0.9
DEFAULT_TEMPORAL_CONNECT_TIMEOUT_SECONDS = 30.0
DEFAULT_TEMPORAL_PROBE_TIMEOUTS_BEFORE_RECONNECT = 3


class AppConfig(BaseSettings):
    """
    TEMPLATE: Application configuration loaded from environment variables

    Essential configuration for Temporal workflow APIs. Teams can add their
    own settings below.
    """

    # TEMPLATE: Core Temporal configuration
    temporal_host: str = Field(
        default="localhost:7233", description="Temporal server host and port"
    )
    temporal_namespace: str = Field(
        default="default", min_length=1, description="Temporal namespace to use"
    )
    temporal_task_queue: str = Field(
        default="workflow-task-queue",
        description="Default task queue for workflows - customize this!",
    )
    temporal_tls_mode: Literal["platform", "tls", "mtls", "off"] = Field(
        default="platform",
        description=(
            "TLS posture for Temporal connections (platform, tls, mtls, or off "
            "for a local server with no TLS, which behaves like platform)"
        ),
    )

    # TEMPLATE: Temporal TLS / mTLS configuration (paths to PEM files).
    # Ignored by foundry-temporal under "platform" and "off", so a deployment can share
    # TEMPORAL_TLS_* variables across components without breaking this API.
    temporal_tls_cert: str | None = Field(
        default=None,
        description="Path to client TLS certificate for Temporal frontend connection",
    )
    temporal_tls_key: str | None = Field(
        default=None,
        description="Path to client TLS private key for Temporal frontend connection",
    )
    temporal_tls_ca: str | None = Field(
        default=None,
        description="Path to CA certificate for verifying the Temporal frontend",
    )

    @field_validator("temporal_tls_cert", "temporal_tls_key", "temporal_tls_ca")
    @classmethod
    def _blank_tls_path_is_unset(cls, value: str | None) -> str | None:
        """Treat a blank path as unset, so an empty env var never reaches open('')."""
        if value is None or not value.strip():
            return None
        return value.strip()

    @model_validator(mode="after")
    def _validate_tls_material(self) -> Self:
        """Fail at startup when the TLS posture's certificate material is missing."""
        if self.temporal_tls_mode == "mtls" and not (
            self.temporal_tls_cert and self.temporal_tls_key
        ):
            raise ValueError(
                "TEMPORAL_TLS_MODE=mtls requires TEMPORAL_TLS_CERT and TEMPORAL_TLS_KEY"
            )
        # A client cert is optional under "tls", but a cert without its key is unusable.
        if self.temporal_tls_mode == "tls" and bool(self.temporal_tls_cert) != bool(
            self.temporal_tls_key
        ):
            raise ValueError(
                "TEMPORAL_TLS_CERT and TEMPORAL_TLS_KEY must be set together"
            )
        # Fail the pod on a missing or unreadable file. A malformed PEM file is
        # not checked here; it fails each connection attempt instead.
        if self.temporal_tls_mode in ("tls", "mtls"):
            for env_name, path in (
                ("TEMPORAL_TLS_CERT", self.temporal_tls_cert),
                ("TEMPORAL_TLS_KEY", self.temporal_tls_key),
                ("TEMPORAL_TLS_CA", self.temporal_tls_ca),
            ):
                if path is not None and not (
                    Path(path).is_file() and os.access(path, os.R_OK)
                ):
                    raise ValueError(
                        f"{env_name} file is missing or unreadable: {path}"
                    )
        return self

    # TEMPLATE: API Configuration
    api_host: str = Field(
        default="127.0.0.1",
        description="API host to bind to",
    )
    api_port: int = Field(default=8000, description="API port to bind to")
    trusted_proxy_hosts: str = Field(
        default="127.0.0.1",
        description=(
            "Comma-separated IPs/CIDRs of proxies (load balancer, ingress) "
            "allowed to set X-Forwarded-For. Required for per-client rate "
            "limiting (see require_api_key) to see real client IPs instead "
            "of the proxy's."
        ),
    )
    # Defaults to foundry-agent-core's shared dict byte cap (_MAX_DICT_BYTES) so
    # the template adds no size of its own; adjust per expected request size.
    max_request_body_bytes: int = Field(
        default=_MAX_DICT_BYTES,
        gt=0,
        description=(
            "Largest request body accepted, in bytes. Larger requests are "
            "rejected with HTTP 413 before the body is fully read."
        ),
    )
    api_auth_token: str | None = Field(
        default=None,
        description=(
            "Bearer token required for /workflows/* endpoints. "
            "Requests are rejected when unset (fail-closed)."
        ),
    )

    @field_validator("api_auth_token")
    @classmethod
    def _validate_api_auth_token_strength(cls, value: str | None) -> str | None:
        """Treat an empty token as unset, and reject a configured token that
        is too weak to resist guessing.

        env.template ships API_AUTH_TOKEN= (empty), so an empty string must
        be treated the same as the field never being set, rather than
        failing the length check and crashing startup.
        """
        if value == "":
            return None
        if value is not None and len(value) < MIN_API_AUTH_TOKEN_LENGTH:
            raise ValueError(
                f"api_auth_token must be at least {MIN_API_AUTH_TOKEN_LENGTH} "
                "characters long"
            )
        return value

    # TEMPLATE: Client configuration
    temporal_probe_timeout_seconds: float = Field(
        default=DEFAULT_TEMPORAL_PROBE_TIMEOUT_SECONDS,
        gt=0,
        allow_inf_nan=False,
        description=(
            "Timeout in seconds for each health check's Temporal work, covering "
            "the connection wait and the probe query. Keep it below the "
            "readiness probe's own timeout"
        ),
    )
    temporal_connect_timeout_seconds: float = Field(
        default=DEFAULT_TEMPORAL_CONNECT_TIMEOUT_SECONDS,
        gt=0,
        allow_inf_nan=False,
        description=(
            "Time limit in seconds for one shared connect to Temporal. A connect "
            "that takes longer fails, so the next request starts a fresh one"
        ),
    )
    temporal_probe_timeouts_before_reconnect: int = Field(
        default=DEFAULT_TEMPORAL_PROBE_TIMEOUTS_BEFORE_RECONNECT,
        ge=1,
        description=(
            "Readiness probe timeouts in a row on the same cached Temporal client "
            "before it is discarded, so the next request connects again"
        ),
    )

    # TEMPLATE: Logging configuration
    log_level: str = Field(
        default="INFO", description="Logging level (DEBUG, INFO, WARNING, ERROR)"
    )

    # TEMPLATE: External services configuration (for API integrations)
    agent_timeout_seconds: int = Field(
        default=60, description="Default timeout for agent calls in seconds"
    )

    # TEMPLATE: Environment configuration
    environment: str = Field(
        default="development",
        description="Application environment (development, staging, production)",
    )

    # TEMPLATE: Teams add their custom configuration here
    # Example custom settings that teams might need:
    # custom_api_url: Optional[str] = Field(
    #     default=None,
    #     description="Custom API URL for your business logic integration"
    # )
    # custom_database_url: Optional[str] = Field(
    #     default=None,
    #     description="Database connection URL for your application"
    # )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # Ignore extra env vars (like FOUNDRY_TEMPORAL_INSTALL_PATH)
        hide_input_in_errors=True,  # Keep API_AUTH_TOKEN out of startup errors
    )

    @property
    def is_production(self) -> bool:
        """Check if running in production environment"""
        return self.environment.lower() == "production"

    @property
    def is_development(self) -> bool:
        """Check if running in development environment"""
        return self.environment.lower() == "development"


# Global configuration instance
_config: AppConfig | None = None


def get_config() -> AppConfig:
    """
    Get global configuration instance (singleton pattern)

    Returns the same AppConfig instance across the application lifecycle.
    """
    global _config

    if _config is None:
        _config = AppConfig()

    return _config


def is_test_flag_enabled() -> bool:
    """
    Check whether the non-production test flag is set.

    This is the sole signal (besides environment) that may activate the
    mock Temporal client and mock health responses - see
    is_mock_mode_enabled().
    """
    return (
        os.environ.get("INTEGRATION_TESTING") == "true"
        or os.environ.get("TESTING") == "true"
    )


def is_mock_mode_enabled(config: AppConfig | None = None) -> bool:
    """
    Whether the mock Temporal client / mock health responses may activate.

    Requires both the test flag AND a non-production environment, so an
    environment string alone never activates the mock client.
    """
    config = config or get_config()
    return is_test_flag_enabled() and not config.is_production


def assert_mock_mode_safe(config: AppConfig | None = None) -> None:
    """Fail fast if the test flag is enabled in a production configuration."""
    config = config or get_config()
    if is_test_flag_enabled() and config.is_production:
        raise RuntimeError(
            "TESTING/INTEGRATION_TESTING is set but environment=production; "
            "refusing to start with mock mode enabled in production"
        )


def assert_tls_mode_safe(config: AppConfig | None = None) -> None:
    """Fail fast if TEMPORAL_TLS_MODE=off is set in a production configuration.

    "off" is a plaintext Temporal connection meant for a local server. In
    production, use "platform" when the platform terminates TLS.
    """
    config = config or get_config()
    if config.temporal_tls_mode == "off" and config.is_production:
        raise RuntimeError(
            "TEMPORAL_TLS_MODE=off is set but environment=production; "
            "refusing to start with a plaintext Temporal connection in production"
        )


def warn_if_tls_paths_ignored(config: AppConfig | None = None) -> None:
    """Warn when TLS paths are set but unused because the mode is "platform" or "off".

    Shared TEMPORAL_TLS_* paths are allowed under those modes, but a forgotten
    TEMPORAL_TLS_MODE would otherwise drop them without a trace. Call this
    after structured logging is set up, so the warning uses the log format.
    """
    config = config or get_config()
    if config.temporal_tls_mode in ("platform", "off") and (
        config.temporal_tls_cert or config.temporal_tls_key or config.temporal_tls_ca
    ):
        from foundry_temporal.logging import get_logger

        get_logger().bind(component="api.config").warning(
            "TEMPORAL_TLS_CERT, TEMPORAL_TLS_KEY or TEMPORAL_TLS_CA is set but "
            f"ignored because TEMPORAL_TLS_MODE is {config.temporal_tls_mode}"
        )
