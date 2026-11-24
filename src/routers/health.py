# Copyright 2026 Booz Allen Hamilton Inc.
# SPDX-License-Identifier: Apache-2.0
"""
TEMPLATE: Basic health check endpoints

Customize health checks for your specific dependencies and requirements.
"""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

from fastapi import APIRouter, Response, status
from temporalio.client import Client
from temporalio.service import RPCError, RPCStatusCode

from ..config import get_config, is_mock_mode_enabled
from ..dependencies import (
    TemporalConnectTimeoutError,
    acquire_temporal_client,
    evict_on_connection_failure,
    record_probe_success,
    record_probe_timeout,
)
from ..models import HealthCheckResponse
from ..version import APP_VERSION

if TYPE_CHECKING:
    from foundry_temporal.logging import StandardLoggerWrapper

router = APIRouter()


class TemporalProbeTimeoutError(TimeoutError):
    """A health probe got no answer from Temporal within the probe timeout."""

    def __init__(
        self, timeout_seconds: float, budget_seconds: float | None = None
    ) -> None:
        if budget_seconds is None:
            budget_seconds = timeout_seconds
        connect_seconds = budget_seconds - timeout_seconds
        if connect_seconds < 0.05 * budget_seconds:
            # The probe had (almost) the whole budget, so name the configured value.
            message = f"Temporal probe timed out after {budget_seconds:g}s"
        else:
            # A slow connect used most of the budget, so say what the probe really had.
            message = (
                f"Temporal probe timed out after {round(timeout_seconds, 2):g}s "
                f"({round(connect_seconds, 2):g}s of the {budget_seconds:g}s budget "
                "went to connecting)"
            )
        super().__init__(message)


class _ProbeQuery(StrEnum):
    """The only visibility queries the health probes send."""

    CONNECTIVITY = "WorkflowId = '__connectivity_check__'"
    TEMPORAL_HEALTH = "WorkflowId = '__temporal_health_check__'"


@dataclass(frozen=True)
class _CheckBudget:
    """One health check's time budget: the configured seconds and its deadline."""

    seconds: float
    deadline: float

    @classmethod
    def start(cls) -> _CheckBudget:
        seconds = get_config().temporal_probe_timeout_seconds
        return cls(seconds, asyncio.get_running_loop().time() + seconds)

    def remaining(self) -> float:
        """Seconds left before the deadline, never negative."""
        return max(0.0, self.deadline - asyncio.get_running_loop().time())


async def _acquire_within(budget: _CheckBudget) -> Client:
    """Get the shared client within the budget; a timeout names the configured budget."""
    try:
        return await acquire_temporal_client(timeout=budget.remaining())
    except TemporalConnectTimeoutError as e:
        if e.shared:
            # The connect itself hit TEMPORAL_CONNECT_TIMEOUT_SECONDS; keep that message.
            raise
        raise TemporalConnectTimeoutError(budget.seconds) from e


async def _probe_temporal(
    client: Client,
    query: _ProbeQuery = _ProbeQuery.CONNECTIVITY,
    *,
    budget: _CheckBudget,
    count_timeouts: bool = False,
) -> None:
    """Run one Temporal RPC through the given client within what is left of the budget.

    Only readiness sets count_timeouts, so diagnostic checks called at any rate
    cannot add to the count that discards the client.
    """

    async def _list_one() -> None:
        async for _ in client.list_workflows(query):
            break  # Just need to verify connection works

    probe_seconds = budget.remaining()
    deadline = asyncio.timeout(probe_seconds)
    try:
        async with deadline:
            await _list_one()
    except TimeoutError as e:
        # Name the probe timeout only when it fired; an inner TimeoutError is logged as is.
        if deadline.expired():
            if count_timeouts:
                # Repeated timeouts on one client discard it, so the next check reconnects.
                record_probe_timeout(client)
            raise TemporalProbeTimeoutError(probe_seconds, budget.seconds) from e
        raise
    except RPCError as e:
        evict_on_connection_failure(client, e)
        raise
    record_probe_success(client)


async def _acquire_and_probe(query: _ProbeQuery = _ProbeQuery.CONNECTIVITY) -> None:
    """Get the shared client and probe it, together within one health-check budget."""
    budget = _CheckBudget.start()
    client = await _acquire_within(budget)
    await _probe_temporal(client, query, budget=budget)


ReadinessTemporalState = Literal["connected", "test_mode", "degraded", "disconnected"]


# Probe statuses that show Temporal is up and refused only this query, so routes
# that do not use it still work. Any other failure counts as unreachable, so
# readiness fails closed. Left out on purpose: NOT_FOUND (the namespace does not
# exist) and FAILED_PRECONDITION (the namespace is not active on this cluster)
# break every route, and RESOURCE_EXHAUSTED is retried by the SDK until the probe
# times out, with throttling tolerance set by the readiness probe's threshold.
_TEMPORAL_ANSWERED_STATUSES = frozenset(
    {
        RPCStatusCode.PERMISSION_DENIED,
        RPCStatusCode.INVALID_ARGUMENT,
    }
)


def _probe_failure_state(error: Exception) -> ReadinessTemporalState:
    """Classify a failed connectivity probe: "degraded" only if Temporal answered."""
    if isinstance(error, RPCError) and error.status in _TEMPORAL_ANSWERED_STATUSES:
        return "degraded"
    return "disconnected"


def _readiness_failure(
    response: Response,
    logger: StandardLoggerWrapper,
    state: ReadinessTemporalState,
    error: Exception,
) -> HealthCheckResponse:
    """Log a failed readiness check at its level and build the response."""
    log = logger.warning if state == "degraded" else logger.error
    log(f"Readiness check failed: {type(error).__name__}: {error}")
    return _readiness_response(response, state)


def _readiness_response(
    response: Response, temporal: ReadinessTemporalState
) -> HealthCheckResponse:
    """Build the readiness body, setting 503 only when Temporal is disconnected.

    Bodies carry only fixed strings, never connection details.
    """
    ready = temporal != "disconnected"
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthCheckResponse(
        status="healthy" if ready else "unhealthy",
        service="workflow-api",
        timestamp=datetime.now(UTC),
        version=APP_VERSION,
        dependencies={"temporal": temporal},
    )


@router.get(
    "",
    response_model=HealthCheckResponse,
    summary="Health check",
    description="Basic API health status",
)
async def health_check() -> HealthCheckResponse:
    """TEMPLATE: Basic API health check - customize for your API dependencies."""

    # Include basic temporal status in test environment
    dependencies = {}
    if is_mock_mode_enabled():
        dependencies["temporal"] = {
            "service": "temporal",
            "status": "test_mode",
        }

    # API is always healthy - this is the basic API health endpoint
    return HealthCheckResponse(
        status="healthy",
        service="workflow-api",
        timestamp=datetime.now(UTC),
        version=APP_VERSION,
        dependencies=dependencies,
    )


@router.get(
    "/detailed",
    response_model=HealthCheckResponse,
    summary="Detailed health check",
    description="Comprehensive health status with all dependency details",
)
async def detailed_health_check() -> HealthCheckResponse:
    """TEMPLATE: Detailed health check - comprehensive dependency verification."""
    from foundry_temporal.logging import get_logger

    logger = get_logger().bind(component="api.health.detailed")

    if is_mock_mode_enabled():
        # Test-friendly detailed health check
        temporal_status = {
            "service": "temporal",
            "status": "test_mode",
            "host": "test-temporal",
            "namespace": "test-namespace",
        }
    else:
        # Full connectivity check for non-test environments
        try:
            await _acquire_and_probe()
            temporal_status = {
                "service": "temporal",
                "status": "healthy",
                "connection": "verified",
            }
            logger.info("Detailed health check - Temporal connection successful")

        except Exception as e:
            temporal_status = {
                "service": "temporal",
                "status": "unhealthy",
            }
            logger.error(
                "Detailed health check - Temporal connection failed: "
                f"{type(e).__name__}: {e}"
            )

    # API is always healthy - graceful degradation
    return HealthCheckResponse(
        status="healthy",
        service="workflow-api",
        timestamp=datetime.now(UTC),
        version=APP_VERSION,
        dependencies={"temporal": temporal_status},
    )


@router.get(
    "/temporal",
    response_model=HealthCheckResponse,
    summary="Temporal connectivity check",
    description="Check Temporal server connectivity status",
)
async def temporal_health_check() -> HealthCheckResponse:
    """TEMPLATE: Temporal-specific health check - customize for your requirements."""
    from foundry_temporal.logging import get_logger

    logger = get_logger().bind(component="api.health.temporal")

    # In test environment, return mock status
    if is_mock_mode_enabled():
        return HealthCheckResponse(
            status="healthy",
            service="temporal",
            timestamp=datetime.now(UTC),
            version=APP_VERSION,
            dependencies={},
        )

    try:
        await _acquire_and_probe(_ProbeQuery.TEMPORAL_HEALTH)

        logger.info("Temporal health check passed")

        return HealthCheckResponse(
            status="healthy",
            service="temporal",
            timestamp=datetime.now(UTC),
            version=APP_VERSION,
            dependencies={},
        )

    except Exception as e:
        logger.error(f"Temporal health check failed: {type(e).__name__}: {e}")
        return HealthCheckResponse(
            status="unhealthy",
            service="temporal",
            timestamp=datetime.now(UTC),
            version=APP_VERSION,
            dependencies={},
        )


@router.get(
    "/ready",
    response_model=HealthCheckResponse,
    summary="Readiness check",
    description=(
        "Kubernetes readiness probe. Returns 503 when Temporal is unreachable "
        "(connect failure, probe timeout, or a probe status that does not show "
        "Temporal handled the request), and 200 with dependencies.temporal "
        "'degraded' when Temporal rejects the probe."
    ),
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": HealthCheckResponse,
            "description": "Temporal unreachable; not ready to receive traffic",
        }
    },
)
async def readiness_check(response: Response) -> HealthCheckResponse:
    """TEMPLATE: Readiness check - customize for your required services."""
    from foundry_temporal.logging import get_logger

    logger = get_logger().bind(component="api.health.ready")

    # In test environment, always return ready
    if is_mock_mode_enabled():
        # The first resolve logs the mock-client activation warning; no server is contacted.
        await acquire_temporal_client()
        return _readiness_response(response, "test_mode")

    # Getting the client and probing it are separate steps within one time
    # budget: any failure to get a client means Temporal is unreachable, and a
    # probe failure is classified by whether Temporal answered.
    budget = _CheckBudget.start()
    try:
        client = await _acquire_within(budget)
    except Exception as e:
        return _readiness_failure(response, logger, "disconnected", e)
    try:
        await _probe_temporal(client, budget=budget, count_timeouts=True)
    except Exception as e:
        return _readiness_failure(response, logger, _probe_failure_state(e), e)

    logger.info("Readiness check passed")
    return _readiness_response(response, "connected")
