# Copyright 2026 Booz Allen Hamilton Inc.
# SPDX-License-Identifier: Apache-2.0
"""
TEMPLATE: FastAPI dependencies for Temporal client

Customize dependencies for your specific requirements.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from foundry_temporal.worker import (
    WorkerConfig,
    close_temporal_client,
    create_temporal_client,
)

# foundry-temporal imports are deferred until runtime when packages are available
from temporalio.client import Client
from temporalio.service import RPCError, RPCStatusCode

from .config import AppConfig, get_config, is_mock_mode_enabled

# RPCError statuses that mean the connection or its credentials are bad,
# not that one specific request was invalid - only these should evict the
# cached client. Shared by every router that calls
# _evict_if_stale so the status check stays in one place.
CONNECTION_FAILURE_STATUSES = (RPCStatusCode.UNAVAILABLE, RPCStatusCode.UNAUTHENTICATED)

# Global client cache
_client_cache: Client | None = None

# The in-flight connect attempt, shared so concurrent callers that find an
# empty cache all await the same attempt instead of each starting their
# own. Checking and setting this must never have an
# `await` in between, so it stays race-free without a lock.
_connect_task: asyncio.Task[Client] | None = None

# How many current callers are still waiting on _connect_task. When this
# drops to zero because every waiter cancelled, the attempt is cancelled
# too instead of left to run on abandoned, so the next caller starts a
# genuinely fresh attempt rather than joining an orphaned one.
_connect_waiters: int = 0

# Set by close_cached_temporal_client() at shutdown, cleared by
# allow_temporal_connects() at startup. A connect that finishes while it is set
# discards its client instead of caching it, since shutdown cleanup has already run.
_shutting_down: bool = False

# The cached client whose health probes are being counted, and how many timed
# out in a row on it. A timeout on any other client starts the count over, so a
# replacement never inherits the old client's count.
_probe_timeout_client: Client | None = None
_probe_timeouts: int = 0


class RateLimiter:
    """Simple in-memory rate limiter for API endpoints."""

    def __init__(self, max_requests: int = 100, window_seconds: int = 60) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.clients: dict[str, list[float]] = {}

    async def check_rate_limit(self, client_id: str) -> bool:
        """Check if client is within rate limit."""
        current_time = time.time()

        if client_id not in self.clients:
            self.clients[client_id] = []

        # Clean old requests
        self.clients[client_id] = [
            req_time
            for req_time in self.clients[client_id]
            if current_time - req_time < self.window_seconds
        ]

        # Check if under limit
        if len(self.clients[client_id]) >= self.max_requests:
            return False

        # Add current request
        self.clients[client_id].append(current_time)
        return True

    def is_over_limit(self, client_id: str) -> bool:
        """Check whether client is at or over the limit, without consuming."""
        current_time = time.time()
        requests = [
            req_time
            for req_time in self.clients.get(client_id, [])
            if current_time - req_time < self.window_seconds
        ]
        self.clients[client_id] = requests
        return len(requests) >= self.max_requests


# Dedicated to failed bearer-token attempts, so legitimate repeated calls
# with a correct token never count against the guess-throttling budget.
_auth_failure_limiter = RateLimiter(max_requests=5, window_seconds=60)


def build_temporal_client_config(config: AppConfig | None = None) -> WorkerConfig:
    """Build the one Temporal client config every connection in the API uses.

    tls_mode comes from the deployer's TEMPORAL_TLS_MODE setting.
    """
    if config is None:
        config = get_config()
    return WorkerConfig(
        temporal_address=config.temporal_host,
        namespace=config.temporal_namespace,
        task_queue=config.temporal_task_queue,
        tls_mode=config.temporal_tls_mode,
        temporal_tls_cert=config.temporal_tls_cert,
        temporal_tls_key=config.temporal_tls_key,
        temporal_tls_ca=config.temporal_tls_ca,
    )


async def get_temporal_client() -> Client:
    """TEMPLATE: Get Temporal client - customize connection parameters as needed.

    FastAPI dependency for the workflow routes: answers 503 when Temporal
    cannot be reached. Code that handles connection errors itself, such as
    the health probes, calls acquire_temporal_client() instead.
    """
    try:
        return await acquire_temporal_client()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to connect to Temporal server",
        ) from e


class TemporalConnectTimeoutError(TimeoutError):
    """A Temporal connect did not finish in time.

    Raised to a caller that stopped waiting at its own timeout, and by the shared
    connect itself when it runs past TEMPORAL_CONNECT_TIMEOUT_SECONDS.
    """

    def __init__(self, timeout_seconds: float, *, shared: bool = False) -> None:
        # shared marks the connect itself hitting its limit, not a caller leaving early.
        self.shared = shared
        message = f"Temporal connect timed out after {timeout_seconds:g}s"
        if shared:
            message += " (TEMPORAL_CONNECT_TIMEOUT_SECONDS)"
        super().__init__(message)


class TemporalConnectCancelledError(ConnectionError):
    """The shared Temporal connect was cancelled, for example by shutdown."""

    def __init__(self) -> None:
        super().__init__("Temporal connect was cancelled before it finished")


def _consume_connect_result(task: asyncio.Task[Client]) -> None:
    """Retrieve a finished connect's exception so an unawaited failure is not logged."""
    if not task.cancelled():
        task.exception()


async def acquire_temporal_client(timeout: float | None = None) -> Client:
    """Return the shared Temporal client, raising the connection error on failure.

    Concurrent callers share one connect attempt, and cancelling the last
    waiting caller also cancels that attempt. Do not wrap this call directly in
    asyncio.wait_for: a connect slower than the timeout would be cancelled on
    every call and could never finish. Pass ``timeout`` instead: the caller
    stops waiting and gets TemporalConnectTimeoutError, while the shared
    attempt keeps running so a later call can reuse it. If the shared attempt
    itself is cancelled, for example by shutdown, a caller that was not
    cancelled gets TemporalConnectCancelledError, an ordinary ConnectionError.
    """
    global _client_cache, _connect_task, _connect_waiters

    # Mock client only when explicitly enabled and not in production
    if is_mock_mode_enabled():
        if _client_cache is None:
            from foundry_temporal.logging import get_logger

            get_logger().bind(component="api.temporal").warning(
                "Mock Temporal client is active - "
                "TESTING/INTEGRATION_TESTING is set and environment is not production"
            )
            mock_client = AsyncMock(spec=Client)
            mock_client.start_workflow = AsyncMock()
            mock_client.get_workflow_handle = AsyncMock()

            # Create an async iterator for list_workflows
            async def mock_list_workflows(
                *args: object, **kwargs: object
            ) -> AsyncGenerator[Any]:
                # Return empty list - no workflows in test environment
                item: Any
                for item in []:
                    yield item

            mock_client.list_workflows = mock_list_workflows
            _client_cache = mock_client
        return _client_cache

    if _client_cache is not None:
        return _client_cache

    # Single-flight: join the in-flight connect attempt if one already
    # exists, rather than starting a second one. No `await` between the
    # check and the assignment, so this is race-free without a lock.
    task = _connect_task
    if task is None:
        task = asyncio.create_task(_connect())
        task.add_done_callback(_consume_connect_result)
        _connect_task = task

    _connect_waiters += 1
    was_cancelled = False
    try:
        # asyncio.wait never cancels the shared attempt, whether this caller
        # times out or is cancelled, and it returns normally when the attempt
        # itself was cancelled, so that case can be reported as a connection error.
        done, _ = await asyncio.wait({task}, timeout=timeout)
        if timeout is not None and not done:
            raise TemporalConnectTimeoutError(timeout)
        if task.cancelled():
            raise TemporalConnectCancelledError
        return task.result()
    except asyncio.CancelledError:
        was_cancelled = True
        raise
    finally:
        _connect_waiters -= 1
        if was_cancelled and _connect_waiters == 0 and _connect_task is task:
            # Every caller that wanted this attempt has given up; cancel
            # it instead of leaving it to run on abandoned, so the next
            # caller starts a genuinely fresh attempt rather than joining
            # (and waiting on) one nobody asked for anymore.
            task.cancel()
            _connect_task = None


async def _connect() -> Client:
    """Perform the actual Temporal connection.

    Runs as a single shared task (see acquire_temporal_client) so concurrent
    callers all await this one attempt instead of each connecting
    separately. Clears _connect_task itself when done, on any outcome -
    success or failure - checking identity first, since acquire_temporal_client
    may have already cleared it (and started a newer task) after every
    waiter cancelled.
    """
    global _client_cache, _connect_task

    from foundry_temporal.logging import get_logger

    logger = get_logger().bind(component="api.temporal")

    connect_timeout = get_config().temporal_connect_timeout_seconds
    try:
        deadline = asyncio.timeout(connect_timeout)
        try:
            async with deadline:
                client = await create_temporal_client(build_temporal_client_config())
        except TimeoutError as e:
            # Name the connect limit only when it fired; an inner TimeoutError stays as is.
            if deadline.expired():
                raise TemporalConnectTimeoutError(connect_timeout, shared=True) from e
            raise
        if _shutting_down:
            # Caching this client now would leave its token-refresh task running
            # with nothing left to cancel it, so discard the client instead.
            close_temporal_client(client)
            raise RuntimeError("Temporal connect finished after shutdown started")
        logger.info("Connected to Temporal server using foundry-temporal")
        _client_cache = client
        return client
    except Exception as e:
        logger.error(f"Failed to connect to Temporal server: {e}")
        raise
    finally:
        this_task = asyncio.current_task()
        if _connect_task is this_task:
            _connect_task = None


def _evict_if_stale(failed_client: Client) -> None:
    """Clear the cached client if it is still the one that just failed.

    No `await` in this function, so it is race-free without a lock:
    whichever concurrent caller runs this first clears the slot; later
    callers see it already cleared and no-op. The identity check means a
    late failure from a superseded client can never discard a newer,
    healthy one that already replaced it. Clearing the slot also cancels the
    evicted client's token-refresh task, which would otherwise keep running,
    and drops its probe timeout count, so nothing keeps the client alive.
    It does not reconnect; the next call to acquire_temporal_client() does
    that through the single-flight path above.
    """
    global _client_cache, _probe_timeout_client, _probe_timeouts
    if _client_cache is failed_client:
        _client_cache = None
        _probe_timeout_client, _probe_timeouts = None, 0
        close_temporal_client(failed_client)


def record_probe_timeout(client: Client) -> bool:
    """Count a health probe timeout on this client, evicting it at the threshold.

    No `await`, like _evict_if_stale. A late timeout from a client that was
    already replaced is ignored, so it cannot reset the cached client's count.
    Returns True when the threshold was reached and the client was evicted.
    """
    global _probe_timeout_client, _probe_timeouts
    if client is not _client_cache:
        return False
    if _probe_timeout_client is not client:
        _probe_timeout_client, _probe_timeouts = client, 0
    _probe_timeouts += 1
    if _probe_timeouts < get_config().temporal_probe_timeouts_before_reconnect:
        return False
    _evict_if_stale(client)
    return True


def record_probe_success(client: Client) -> None:
    """Reset the probe timeout count after this client answered a health probe."""
    global _probe_timeout_client, _probe_timeouts
    if _probe_timeout_client is client:
        _probe_timeout_client, _probe_timeouts = None, 0


async def close_cached_temporal_client(cancel_wait_seconds: float = 1.0) -> None:
    """At shutdown, drop the cached client and cancel its token-refresh task.

    Also cancels a connect still in flight, which would otherwise cache a
    client after shutdown whose refresh task nothing ever cancels. The slot is
    cleared here too: a task cancelled before its first step never runs
    _connect's finally block, and would otherwise be joined by the next caller.
    Waits up to cancel_wait_seconds seconds for that cancel to finish, so shutdown
    does not return with the task still pending. Sets _shutting_down first, so a
    connect that ignores the cancel, or one a still-running request starts after
    this cleanup, discards its client instead of caching it.
    """
    global _client_cache, _connect_task, _shutting_down
    global _probe_timeout_client, _probe_timeouts
    _shutting_down = True
    _probe_timeout_client, _probe_timeouts = None, 0
    pending = _connect_task
    if pending is not None and not pending.done():
        pending.cancel()
    else:
        pending = None
    _connect_task = None
    client, _client_cache = _client_cache, None
    if client is not None:
        close_temporal_client(client)
    if pending is not None:
        _, still_running = await asyncio.wait({pending}, timeout=cancel_wait_seconds)
        if still_running:
            from foundry_temporal.logging import get_logger

            get_logger().bind(component="api.temporal").warning(
                f"Temporal connect still running {cancel_wait_seconds} s after shutdown "
                "cancelled it; its client will be discarded when it finishes"
            )


def allow_temporal_connects() -> None:
    """At startup, let connects cache their client again after an earlier shutdown."""
    global _shutting_down
    _shutting_down = False


def is_connection_failure(error: RPCError) -> bool:
    """Whether this RPC error means the connection itself is unusable."""
    return error.status in CONNECTION_FAILURE_STATUSES


def evict_on_connection_failure(client: Client, error: RPCError) -> bool:
    """Drop the cached client if this error means the connection is broken.

    Returns True when it was a connection failure.
    """
    if is_connection_failure(error):
        _evict_if_stale(client)
        return True
    return False


def get_workflow_config() -> dict[str, str]:
    """TEMPLATE: Get workflow configuration for API endpoints."""
    config = get_config()
    return {
        "task_queue": config.temporal_task_queue,
        "namespace": config.temporal_namespace,
        "temporal_host": config.temporal_host,
        "tls_mode": config.temporal_tls_mode,
    }


_bearer_scheme = HTTPBearer(auto_error=False)


async def require_api_key(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> None:
    """
    Router-level auth dependency for /workflows/* endpoints.

    Fail-closed: rejects every request when API_AUTH_TOKEN is unset, so the
    template never ships with an open HITL approve/signal surface. A client
    with too many recent failed attempts is rejected with 429 before its
    token is even compared, so a locked-out client gets a uniform response
    regardless of what token it sends.
    """
    config = get_config()
    if not config.api_auth_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API authentication is not configured (set API_AUTH_TOKEN)",
        )

    client_id = request.client.host if request.client else "unknown"
    if _auth_failure_limiter.is_over_limit(client_id):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed authentication attempts",
        )

    if credentials is not None and secrets.compare_digest(
        credentials.credentials, config.api_auth_token
    ):
        return

    await _auth_failure_limiter.check_rate_limit(client_id)
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing bearer token",
        headers={"WWW-Authenticate": "Bearer"},
    )
