# Copyright 2026 Booz Allen Hamilton Inc.
# SPDX-License-Identifier: Apache-2.0
"""
Unit tests for FastAPI dependencies
"""

import asyncio
import contextlib
import gc
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException
from temporalio.client import Client

from src.config import AppConfig, get_config
from src.dependencies import (
    RateLimiter,
    TemporalConnectCancelledError,
    TemporalConnectTimeoutError,
    _evict_if_stale,
    acquire_temporal_client,
    allow_temporal_connects,
    build_temporal_client_config,
    close_cached_temporal_client,
    get_temporal_client,
    record_probe_success,
    record_probe_timeout,
)


class TestGetConfig:
    """Test get_config function"""

    def setUp(self):
        """Reset global state before each test"""
        import src.config

        src.config._config = None

    def test_get_config_returns_app_config(self):
        """Test that get_config returns AppConfig instance"""
        import src.config

        src.config._config = None

        config = get_config()
        assert isinstance(config, AppConfig)

    def test_get_config_singleton_behavior(self):
        """Test that get_config returns the same instance"""
        import src.config

        src.config._config = None

        config1 = get_config()
        config2 = get_config()

        assert config1 is config2
        assert id(config1) == id(config2)

    def test_get_config_with_cached_instance(self):
        """Test get_config with pre-cached instance"""
        import src.config

        # Pre-populate cache
        cached_config = AppConfig(api_host="test-host")
        src.config._config = cached_config

        config = get_config()
        assert config is cached_config
        assert config.api_host == "test-host"

    def tearDown(self):
        """Clean up after each test"""
        import src.config

        src.config._config = None


class TestGetTemporalClient:
    """Test get_temporal_client function"""

    def setUp(self):
        """Reset global state before each test"""
        import src.dependencies

        src.dependencies._client_cache = None
        src.config._config = None

    @pytest.mark.asyncio
    async def test_get_temporal_client_success(self):
        """Test successful Temporal client creation"""
        import src.dependencies

        src.dependencies._client_cache = None

        mock_client = AsyncMock()

        with patch(
            "src.dependencies.create_temporal_client", return_value=mock_client
        ) as mock_create_client:
            client = await get_temporal_client()

            assert client is mock_client
            mock_create_client.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_temporal_client_cached(self):
        """Test that cached client is returned"""
        import src.dependencies

        # Pre-populate cache
        cached_client = AsyncMock()
        src.dependencies._client_cache = cached_client

        client = await get_temporal_client()

        assert client is cached_client

    @pytest.mark.asyncio
    async def test_get_temporal_client_connection_failure(self):
        """Test handling of Temporal connection failure"""
        import src.dependencies

        src.dependencies._client_cache = None

        connection_error = Exception("Connection refused")

        with patch(
            "src.dependencies.create_temporal_client", side_effect=connection_error
        ):
            with pytest.raises(HTTPException) as exc_info:
                await get_temporal_client()

            assert exc_info.value.status_code == 503
            assert "Unable to connect to Temporal server" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_acquire_raises_the_connection_error(self, monkeypatch):
        """acquire_temporal_client raises the underlying error, untranslated."""
        import src.dependencies

        monkeypatch.delenv("TESTING", raising=False)
        monkeypatch.delenv("INTEGRATION_TESTING", raising=False)
        monkeypatch.setattr(src.dependencies, "_client_cache", None)
        connection_error = ConnectionError("Connection refused")

        with (
            patch(
                "src.dependencies.create_temporal_client",
                side_effect=connection_error,
            ),
            pytest.raises(ConnectionError) as exc_info,
        ):
            await acquire_temporal_client()

        assert exc_info.value is connection_error

    @pytest.mark.asyncio
    async def test_get_chains_the_connection_error(self, monkeypatch):
        """get_temporal_client's 503 keeps the underlying error as its cause."""
        import src.dependencies

        monkeypatch.delenv("TESTING", raising=False)
        monkeypatch.delenv("INTEGRATION_TESTING", raising=False)
        monkeypatch.setattr(src.dependencies, "_client_cache", None)
        connection_error = ConnectionError("Connection refused")

        with (
            patch(
                "src.dependencies.create_temporal_client",
                side_effect=connection_error,
            ),
            pytest.raises(HTTPException) as exc_info,
        ):
            await get_temporal_client()

        assert exc_info.value.status_code == 503
        assert exc_info.value.__cause__ is connection_error

    @pytest.mark.asyncio
    async def test_cancelling_acquire_propagates(self, monkeypatch):
        """A cancelled acquire_temporal_client caller gets CancelledError."""
        import src.dependencies

        monkeypatch.delenv("TESTING", raising=False)
        monkeypatch.delenv("INTEGRATION_TESTING", raising=False)
        monkeypatch.setattr(src.dependencies, "_client_cache", None)
        monkeypatch.setattr(src.dependencies, "_connect_task", None)
        monkeypatch.setattr(src.dependencies, "_connect_waiters", 0)
        connect_started = asyncio.Event()

        async def blocking_forever(*_args: object, **_kwargs: object) -> AsyncMock:
            connect_started.set()
            await asyncio.sleep(3600)
            raise AssertionError("should have been cancelled before this point")

        with patch(
            "src.dependencies.create_temporal_client", side_effect=blocking_forever
        ):
            waiter = asyncio.ensure_future(acquire_temporal_client())
            await connect_started.wait()
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter

    @pytest.mark.asyncio
    async def test_mock_mode_acquire_and_get_share_the_client(self, monkeypatch):
        """Both entry points return the same mock client in mock mode."""
        import src.dependencies

        monkeypatch.setenv("TESTING", "true")
        monkeypatch.setattr(src.dependencies, "_client_cache", None)

        assert await acquire_temporal_client() is await get_temporal_client()

    @pytest.mark.asyncio
    async def test_get_temporal_client_uses_config(self):
        """Test that client uses configuration values"""
        import src.dependencies

        src.dependencies._client_cache = None
        src.config._config = None

        test_config = AppConfig(
            temporal_host="test-temporal:7233",
            temporal_namespace="test-namespace",
            temporal_tls_mode="tls",
            _env_file=None,
        )

        mock_client = AsyncMock()

        with (
            patch("src.dependencies.get_config", return_value=test_config),
            patch(
                "src.dependencies.create_temporal_client", return_value=mock_client
            ) as mock_create_client,
        ):
            client = await get_temporal_client()

            assert client is mock_client
            # Verify that create_temporal_client was called with a WorkerConfig
            mock_create_client.assert_called_once()
            worker_config = mock_create_client.call_args[0][
                0
            ]  # First positional argument
            assert worker_config.temporal_host == "test-temporal:7233"
            assert worker_config.temporal_namespace == "test-namespace"
            assert worker_config.tls_mode == "tls"

    def tearDown(self):
        """Clean up after each test"""
        import src.dependencies

        src.dependencies._client_cache = None
        src.config._config = None

    @pytest.mark.asyncio
    async def test_mock_mode_logs_warning_once(self, monkeypatch):
        """Mock-mode activation logs a warning."""
        import src.dependencies

        src.dependencies._client_cache = None
        monkeypatch.setenv("TESTING", "true")

        mock_logger = Mock()
        mock_logger.bind.return_value = mock_logger

        with patch("foundry_temporal.logging.get_logger", return_value=mock_logger):
            await get_temporal_client()

        mock_logger.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_warning_when_mock_mode_disabled(self, monkeypatch):
        """No mock-mode warning is logged when the test flag is unset."""
        import src.dependencies

        src.dependencies._client_cache = None
        monkeypatch.delenv("TESTING", raising=False)
        monkeypatch.delenv("INTEGRATION_TESTING", raising=False)

        mock_logger = Mock()
        mock_logger.bind.return_value = mock_logger
        mock_client = AsyncMock()

        with (
            patch("foundry_temporal.logging.get_logger", return_value=mock_logger),
            patch("src.dependencies.create_temporal_client", return_value=mock_client),
        ):
            await get_temporal_client()

        mock_logger.warning.assert_not_called()

    @pytest.mark.asyncio
    async def test_concurrent_calls_connect_once(self):
        """Concurrent callers on an empty cache share one connect attempt
        and all receive the resulting client."""
        import src.dependencies

        src.dependencies._client_cache = None
        src.dependencies._connect_task = None

        mock_client = AsyncMock()

        async def slow_connect(*_args: object, **_kwargs: object) -> AsyncMock:
            await asyncio.sleep(0.05)
            return mock_client

        with patch(
            "src.dependencies.create_temporal_client", side_effect=slow_connect
        ) as mock_create_client:
            results = await asyncio.gather(
                get_temporal_client(),
                get_temporal_client(),
                get_temporal_client(),
            )

        assert all(r is mock_client for r in results)
        mock_create_client.assert_called_once()
        assert src.dependencies._connect_task is None

    @pytest.mark.asyncio
    async def test_concurrent_calls_share_failure(self):
        """Concurrent callers on a failing connect each get their own 503
        from the one shared attempt, not their own separate connects."""
        import src.dependencies

        src.dependencies._client_cache = None
        src.dependencies._connect_task = None

        async def slow_fail(*_args: object, **_kwargs: object) -> AsyncMock:
            await asyncio.sleep(0.05)
            raise RuntimeError("connection refused")

        with patch(
            "src.dependencies.create_temporal_client", side_effect=slow_fail
        ) as mock_create_client:
            results = await asyncio.gather(
                get_temporal_client(),
                get_temporal_client(),
                get_temporal_client(),
                return_exceptions=True,
            )

        assert len(results) == 3
        for result in results:
            assert isinstance(result, HTTPException)
            assert result.status_code == 503
        mock_create_client.assert_called_once()
        assert src.dependencies._client_cache is None
        assert src.dependencies._connect_task is None

    @pytest.mark.asyncio
    async def test_all_waiters_cancelled_allows_fresh_attempt(self):
        """If every waiter cancels its own wait, the abandoned attempt is
        itself cancelled rather than left running orphaned, so a later
        caller starts a genuinely fresh attempt instead of joining (and
        waiting on) one nobody asked for anymore."""
        import src.dependencies

        src.dependencies._client_cache = None
        src.dependencies._connect_task = None
        src.dependencies._connect_waiters = 0

        connect_started = asyncio.Event()

        async def blocking_forever(*_args: object, **_kwargs: object) -> AsyncMock:
            connect_started.set()
            await asyncio.sleep(3600)  # would hang if not actually cancelled
            raise AssertionError("should have been cancelled before this point")

        with patch(
            "src.dependencies.create_temporal_client",
            side_effect=blocking_forever,
        ) as mock_create_client:
            waiter = asyncio.ensure_future(get_temporal_client())
            await connect_started.wait()
            background_task = src.dependencies._connect_task
            assert background_task is not None

            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter

            # The last waiter cancelling must cancel the orphaned attempt
            # too - not leave it running, unobserved, in the background.
            with pytest.raises(asyncio.CancelledError):
                await background_task

        mock_create_client.assert_called_once()
        assert src.dependencies._connect_task is None
        assert src.dependencies._client_cache is None

        mock_client = AsyncMock()
        with patch(
            "src.dependencies.create_temporal_client", return_value=mock_client
        ) as mock_create_client_2:
            client = await get_temporal_client()

        assert client is mock_client
        mock_create_client_2.assert_called_once()

    @pytest.mark.asyncio
    async def test_one_waiter_cancelling_does_not_disrupt_others(self):
        """If some but not all waiters cancel, the shared attempt keeps
        running for the remaining waiter(s) and still succeeds for them."""
        import src.dependencies

        src.dependencies._client_cache = None
        src.dependencies._connect_task = None
        src.dependencies._connect_waiters = 0

        mock_client = AsyncMock()

        async def slow_connect(*_args: object, **_kwargs: object) -> AsyncMock:
            await asyncio.sleep(0.05)
            return mock_client

        with patch(
            "src.dependencies.create_temporal_client", side_effect=slow_connect
        ) as mock_create_client:
            waiter_a = asyncio.ensure_future(get_temporal_client())
            waiter_b = asyncio.ensure_future(get_temporal_client())
            await asyncio.sleep(0)  # let both join the same in-flight attempt

            waiter_a.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter_a

            result_b = await waiter_b

        assert result_b is mock_client
        mock_create_client.assert_called_once()
        assert src.dependencies._connect_waiters == 0


class TestBoundedAcquire:
    """acquire_temporal_client(timeout=...) stops waiting without cancelling the connect."""

    @pytest.fixture(autouse=True)
    def _live(self, monkeypatch):
        import src.dependencies

        monkeypatch.delenv("TESTING", raising=False)
        monkeypatch.delenv("INTEGRATION_TESTING", raising=False)
        monkeypatch.setattr(src.dependencies, "_client_cache", None)
        monkeypatch.setattr(src.dependencies, "_connect_task", None)
        monkeypatch.setattr(src.dependencies, "_connect_waiters", 0)

    @staticmethod
    async def _cancel_shared_task():
        import src.dependencies

        task = src.dependencies._connect_task
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    @pytest.mark.asyncio
    async def test_hung_connect_times_out_without_cancelling_it(self):
        import src.dependencies

        started = asyncio.Event()

        async def hang(*_args, **_kwargs):
            started.set()
            await asyncio.sleep(3600)

        with patch("src.dependencies.create_temporal_client", side_effect=hang):
            try:
                with pytest.raises(TemporalConnectTimeoutError) as exc_info:
                    await acquire_temporal_client(timeout=0.05)
                task = src.dependencies._connect_task
                assert started.is_set()
                assert task is not None and not task.done()
                assert "timed out after 0.05s" in str(exc_info.value)
            finally:
                await self._cancel_shared_task()

    @pytest.mark.asyncio
    async def test_slow_connect_completes_after_a_bounded_caller_gave_up(self):
        import src.dependencies

        release = asyncio.Event()
        client = AsyncMock()

        async def slow(*_args, **_kwargs):
            await release.wait()
            return client

        with patch(
            "src.dependencies.create_temporal_client", side_effect=slow
        ) as mock_create:
            with pytest.raises(TemporalConnectTimeoutError):
                await acquire_temporal_client(timeout=0.01)
            task = src.dependencies._connect_task
            release.set()
            assert await task is client
            assert await acquire_temporal_client(timeout=0.01) is client

        mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_concurrent_bounded_callers_share_one_uncancelled_connect(self):
        import src.dependencies

        async def hang(*_args, **_kwargs):
            await asyncio.sleep(3600)

        with patch(
            "src.dependencies.create_temporal_client", side_effect=hang
        ) as mock_create:
            try:
                results = await asyncio.gather(
                    *(acquire_temporal_client(timeout=0.02) for _ in range(3)),
                    return_exceptions=True,
                )
                task = src.dependencies._connect_task
                assert all(isinstance(r, TemporalConnectTimeoutError) for r in results)
                assert task is not None and not task.done()
                assert src.dependencies._connect_waiters == 0
                mock_create.assert_called_once()
            finally:
                await self._cancel_shared_task()

    @pytest.mark.asyncio
    async def test_cancelled_bounded_caller_still_cancels_an_abandoned_connect(self):
        import src.dependencies

        started = asyncio.Event()

        async def hang(*_args, **_kwargs):
            started.set()
            await asyncio.sleep(3600)

        with patch("src.dependencies.create_temporal_client", side_effect=hang):
            waiter = asyncio.ensure_future(acquire_temporal_client(timeout=10))
            await started.wait()
            task = src.dependencies._connect_task
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            with pytest.raises(asyncio.CancelledError):
                await task

        assert src.dependencies._connect_task is None

    @pytest.mark.asyncio
    async def test_failure_after_waiters_left_is_not_reported_as_unretrieved(self):
        import src.dependencies

        release = asyncio.Event()
        unretrieved = []
        loop = asyncio.get_running_loop()
        previous_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: unretrieved.append(context))

        async def fail_later(*_args, **_kwargs):
            await release.wait()
            raise ConnectionError("refused")

        try:
            with patch(
                "src.dependencies.create_temporal_client", side_effect=fail_later
            ):
                with pytest.raises(TemporalConnectTimeoutError):
                    await acquire_temporal_client(timeout=0.01)
                task = src.dependencies._connect_task
                release.set()
                with contextlib.suppress(ConnectionError):
                    await asyncio.wait({task})
                del task
                gc.collect()
        finally:
            loop.set_exception_handler(previous_handler)

        assert unretrieved == []

    @pytest.mark.asyncio
    async def test_get_temporal_client_still_waits_without_a_timeout(self, monkeypatch):
        monkeypatch.setenv("TEMPORAL_PROBE_TIMEOUT_SECONDS", "0.01")
        client = AsyncMock()

        async def slow(*_args, **_kwargs):
            await asyncio.sleep(0.1)
            return client

        with patch("src.dependencies.create_temporal_client", side_effect=slow):
            assert await get_temporal_client() is client


class TestSharedConnectLimits:
    """The shared connect has its own time limit, and a cancelled shared connect
    reaches waiting callers as an ordinary connection error."""

    @pytest.fixture(autouse=True)
    def _fresh_connect_state(self, monkeypatch):
        import src.dependencies

        monkeypatch.setattr(src.dependencies, "_client_cache", None)
        monkeypatch.setattr(src.dependencies, "_connect_task", None)
        monkeypatch.setattr(src.dependencies, "_connect_waiters", 0)

    @pytest.mark.asyncio
    async def test_hung_connect_fails_at_the_limit_and_the_next_call_starts_fresh(
        self, monkeypatch
    ):
        import src.dependencies

        monkeypatch.setenv("TEMPORAL_CONNECT_TIMEOUT_SECONDS", "0.05")
        never = asyncio.Event()

        async def hung_connect(*_args, **_kwargs):
            await never.wait()

        with patch(
            "src.dependencies.create_temporal_client", side_effect=hung_connect
        ) as mock_create:
            for attempt in (1, 2):
                with pytest.raises(TemporalConnectTimeoutError) as exc_info:
                    await asyncio.wait_for(acquire_temporal_client(), timeout=1)
                assert exc_info.value.shared
                assert "(TEMPORAL_CONNECT_TIMEOUT_SECONDS)" in str(exc_info.value)
                assert src.dependencies._connect_task is None
                assert mock_create.await_count == attempt

    @pytest.mark.asyncio
    async def test_inner_timeout_error_is_not_relabelled(self, monkeypatch):
        """A TimeoutError raised inside the connect, before the limit, stays as is."""
        monkeypatch.setenv("TEMPORAL_CONNECT_TIMEOUT_SECONDS", "30")

        with (
            patch(
                "src.dependencies.create_temporal_client",
                side_effect=TimeoutError("token endpoint timed out"),
            ),
            pytest.raises(TimeoutError, match="token endpoint timed out") as exc_info,
        ):
            await asyncio.wait_for(acquire_temporal_client(), timeout=1)
        assert not isinstance(exc_info.value, TemporalConnectTimeoutError)

    @pytest.mark.asyncio
    async def test_caller_timeout_is_not_marked_shared(self):
        import src.dependencies

        src.dependencies._connect_task = asyncio.create_task(asyncio.sleep(5))
        try:
            with pytest.raises(TemporalConnectTimeoutError) as exc_info:
                await acquire_temporal_client(timeout=0.01)
            assert not exc_info.value.shared
        finally:
            src.dependencies._connect_task.cancel()

    @pytest.mark.parametrize("timeout", [None, 1.0])
    @pytest.mark.asyncio
    async def test_cancelled_shared_connect_is_a_connection_error(self, timeout):
        """A caller that was not cancelled gets TemporalConnectCancelledError,
        an ordinary ConnectionError, not CancelledError."""
        import src.dependencies

        shared = asyncio.create_task(asyncio.sleep(5))
        src.dependencies._connect_task = shared
        waiter = asyncio.create_task(acquire_temporal_client(timeout=timeout))
        await asyncio.sleep(0)  # let the waiter start waiting
        shared.cancel()

        with pytest.raises(TemporalConnectCancelledError):
            await asyncio.wait_for(waiter, timeout=1)
        assert issubclass(TemporalConnectCancelledError, ConnectionError)

    @pytest.mark.asyncio
    async def test_cancelled_caller_still_gets_cancelled_error(self):
        import src.dependencies

        shared = asyncio.create_task(asyncio.sleep(5))
        src.dependencies._connect_task = shared
        waiter = asyncio.create_task(acquire_temporal_client())
        await asyncio.sleep(0)
        waiter.cancel()
        try:
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(waiter, timeout=1)
        finally:
            shared.cancel()


class TestEvictIfStale:
    """Test _evict_if_stale function"""

    def test_evicts_matching_client(self) -> None:
        """Clears the cache when it still holds the failed client, and closes
        that client once."""
        import src.dependencies

        failed_client = AsyncMock()
        src.dependencies._client_cache = failed_client

        with patch("src.dependencies.close_temporal_client") as mock_close:
            _evict_if_stale(failed_client)

        assert src.dependencies._client_cache is None
        mock_close.assert_called_once_with(failed_client)

    def test_does_not_evict_different_client(self) -> None:
        """Never discards or closes a newer client that already replaced the
        failed one."""
        import src.dependencies

        old_client = AsyncMock()
        newer_client = AsyncMock()
        src.dependencies._client_cache = newer_client

        with patch("src.dependencies.close_temporal_client") as mock_close:
            _evict_if_stale(old_client)

        assert src.dependencies._client_cache is newer_client
        mock_close.assert_not_called()

    def test_noop_when_cache_already_empty(self) -> None:
        """Idempotent when the cache is already empty, and closes nothing."""
        import src.dependencies

        src.dependencies._client_cache = None

        with patch("src.dependencies.close_temporal_client") as mock_close:
            _evict_if_stale(AsyncMock())

        assert src.dependencies._client_cache is None
        mock_close.assert_not_called()

    @pytest.mark.asyncio
    async def test_cancels_evicted_clients_token_refresh_task(self) -> None:
        """With the real close_temporal_client, eviction cancels the token
        refresh task foundry-temporal attached to the client."""
        import src.dependencies

        refresh_task = asyncio.create_task(asyncio.sleep(5))
        failed_client = Mock()
        failed_client._foundry_refresh_task = refresh_task
        src.dependencies._client_cache = failed_client

        _evict_if_stale(failed_client)
        # The timeout makes a missed cancel fail in 1 second instead of waiting out the sleep.
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(refresh_task, timeout=1)

        assert refresh_task.cancelled()
        assert src.dependencies._client_cache is None


class TestRecordProbeTimeout:
    """Repeated probe timeouts on the same cached client evict it."""

    @pytest.fixture(autouse=True)
    def _threshold_of_three(self, monkeypatch):
        monkeypatch.setenv("TEMPORAL_PROBE_TIMEOUTS_BEFORE_RECONNECT", "3")

    def test_evicts_at_the_threshold_and_not_before(self) -> None:
        import src.dependencies

        client = Mock()
        src.dependencies._client_cache = client

        with patch("src.dependencies.close_temporal_client") as mock_close:
            assert not record_probe_timeout(client)
            assert not record_probe_timeout(client)
            assert src.dependencies._client_cache is client
            mock_close.assert_not_called()

            assert record_probe_timeout(client)

        assert src.dependencies._client_cache is None
        mock_close.assert_called_once_with(client)

    def test_success_resets_the_count(self) -> None:
        import src.dependencies

        client = Mock()
        src.dependencies._client_cache = client

        with patch("src.dependencies.close_temporal_client"):
            record_probe_timeout(client)
            record_probe_timeout(client)
            record_probe_success(client)
            assert not record_probe_timeout(client)
            assert not record_probe_timeout(client)

        assert src.dependencies._client_cache is client

    def test_success_on_another_client_keeps_the_count(self) -> None:
        import src.dependencies

        client = Mock()
        src.dependencies._client_cache = client

        with patch("src.dependencies.close_temporal_client"):
            record_probe_timeout(client)
            record_probe_timeout(client)
            record_probe_success(Mock())
            assert record_probe_timeout(client)

        assert src.dependencies._client_cache is None

    def test_a_new_client_starts_its_own_count(self) -> None:
        import src.dependencies

        old_client, new_client = Mock(), Mock()
        src.dependencies._client_cache = old_client

        with patch("src.dependencies.close_temporal_client"):
            record_probe_timeout(old_client)
            record_probe_timeout(old_client)
            src.dependencies._client_cache = new_client
            assert not record_probe_timeout(new_client)
            assert not record_probe_timeout(new_client)

        assert src.dependencies._client_cache is new_client

    def test_a_replaced_client_is_not_evicted_again(self) -> None:
        """A timeout for a client that was already replaced leaves the newer
        client cached and closes nothing."""
        import src.dependencies

        old_client, newer_client = Mock(), Mock()
        src.dependencies._client_cache = old_client

        with patch("src.dependencies.close_temporal_client") as mock_close:
            record_probe_timeout(old_client)
            record_probe_timeout(old_client)
            src.dependencies._client_cache = newer_client
            assert not record_probe_timeout(old_client)

        assert src.dependencies._client_cache is newer_client
        mock_close.assert_not_called()

    def test_a_late_timeout_from_a_replaced_client_keeps_the_count(self) -> None:
        """A probe on the old client that times out after the swap does not
        reset the timeouts already counted on the cached client."""
        import src.dependencies

        old_client, newer_client = Mock(), Mock()
        src.dependencies._client_cache = newer_client

        with patch("src.dependencies.close_temporal_client") as mock_close:
            record_probe_timeout(newer_client)
            record_probe_timeout(newer_client)
            assert not record_probe_timeout(old_client)
            assert record_probe_timeout(newer_client)

        assert src.dependencies._client_cache is None
        mock_close.assert_called_once_with(newer_client)

    def test_connection_failure_eviction_drops_the_count(self) -> None:
        """Evicting a counted client for a connection error clears its count,
        so the module no longer holds the evicted client."""
        import src.dependencies

        client = Mock()
        src.dependencies._client_cache = client

        with patch("src.dependencies.close_temporal_client"):
            record_probe_timeout(client)
            _evict_if_stale(client)

        assert src.dependencies._probe_timeout_client is None
        assert src.dependencies._probe_timeouts == 0

    @pytest.mark.asyncio
    async def test_shutdown_drops_the_count(self) -> None:
        """Shutdown clears the count along with the cached client."""
        import src.dependencies

        client = Mock()
        src.dependencies._client_cache = client
        src.dependencies._connect_task = None

        with patch("src.dependencies.close_temporal_client"):
            record_probe_timeout(client)
            await close_cached_temporal_client()

        assert src.dependencies._probe_timeout_client is None
        assert src.dependencies._probe_timeouts == 0

    @pytest.mark.asyncio
    async def test_eviction_cancels_the_token_refresh_task(self) -> None:
        """With the real close_temporal_client, the evicted client's token
        refresh task is cancelled."""
        import src.dependencies

        refresh_task = asyncio.create_task(asyncio.sleep(5))
        client = Mock()
        client._foundry_refresh_task = refresh_task
        src.dependencies._client_cache = client

        for _ in range(3):
            record_probe_timeout(client)
        # The timeout makes a missed cancel fail in 1 second instead of waiting out the sleep.
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(refresh_task, timeout=1)

        assert refresh_task.cancelled()
        assert src.dependencies._client_cache is None


class TestCloseCachedTemporalClient:
    """Shutdown closes the cached client."""

    @pytest.mark.asyncio
    async def test_clears_and_closes_cached_client(self) -> None:
        """Clears the cache and closes the cached client once."""
        import src.dependencies

        cached_client = AsyncMock()
        src.dependencies._client_cache = cached_client

        with patch("src.dependencies.close_temporal_client") as mock_close:
            await close_cached_temporal_client()

        assert src.dependencies._client_cache is None
        mock_close.assert_called_once_with(cached_client)

    @pytest.mark.asyncio
    async def test_noop_when_cache_empty(self) -> None:
        """Closes nothing when no client is cached."""
        import src.dependencies

        src.dependencies._client_cache = None

        with patch("src.dependencies.close_temporal_client") as mock_close:
            await close_cached_temporal_client()

        mock_close.assert_not_called()

    @pytest.mark.asyncio
    async def test_cancels_and_waits_for_connect_still_in_flight(self) -> None:
        """A connect still running at shutdown is cancelled and finished before
        the helper returns, so it cannot cache a client afterwards and is not
        left pending."""
        import src.dependencies

        connect_task = asyncio.create_task(asyncio.sleep(5))
        src.dependencies._client_cache = None
        src.dependencies._connect_task = connect_task
        try:
            # The timeout makes a missed cancel fail in 1 second instead of waiting out the sleep.
            await asyncio.wait_for(close_cached_temporal_client(), timeout=1)
            assert src.dependencies._connect_task is None
            assert connect_task.cancelled()
        finally:
            src.dependencies._connect_task = None

    @pytest.mark.asyncio
    async def test_connect_cancelled_before_it_starts_does_not_block_next_connect(
        self,
    ) -> None:
        """A real _connect task cancelled before its first step never runs its
        finally block. Shutdown still clears the slot, so after a restart the
        next caller starts a fresh connect instead of joining the cancelled task."""
        import src.dependencies

        src.dependencies._client_cache = None
        src.dependencies._connect_task = asyncio.create_task(
            src.dependencies._connect()
        )
        try:
            await asyncio.wait_for(close_cached_temporal_client(), timeout=1)
            assert src.dependencies._connect_task is None

            allow_temporal_connects()  # the app starting up again in this process
            fresh_client = AsyncMock(spec=Client)
            with patch(
                "src.dependencies.create_temporal_client", return_value=fresh_client
            ) as mock_create_client:
                client = await asyncio.wait_for(acquire_temporal_client(), timeout=1)

            assert client is fresh_client
            mock_create_client.assert_called_once()
        finally:
            src.dependencies._client_cache = None
            src.dependencies._connect_task = None

    @pytest.mark.asyncio
    async def test_leaves_finished_connect_alone(self) -> None:
        """A connect that already finished is not touched."""
        import src.dependencies

        connect_task = asyncio.create_task(asyncio.sleep(0))
        await connect_task
        src.dependencies._client_cache = None
        src.dependencies._connect_task = connect_task
        try:
            await close_cached_temporal_client()
            assert connect_task.done() and not connect_task.cancelled()
        finally:
            src.dependencies._connect_task = None

    @pytest.mark.asyncio
    async def test_connect_finishing_after_shutdown_discards_its_client(
        self,
    ) -> None:
        """A connect that finishes after shutdown cleanup ran, because it ignored
        the cancel or a still-running request started it, discards its client
        and cancels its token-refresh task instead of caching it."""
        import src.dependencies

        src.dependencies._client_cache = None
        await close_cached_temporal_client()

        late_client = Mock()
        with (
            patch("src.dependencies.create_temporal_client", return_value=late_client),
            patch("src.dependencies.close_temporal_client") as mock_close,
            pytest.raises(HTTPException) as exc_info,
        ):
            await asyncio.wait_for(get_temporal_client(), timeout=1)

        assert exc_info.value.status_code == 503
        assert src.dependencies._client_cache is None
        mock_close.assert_called_once_with(late_client)

    @pytest.mark.asyncio
    async def test_connects_cache_again_after_restart(self) -> None:
        """After allow_temporal_connects() at startup, a connect caches its
        client as usual."""
        import src.dependencies

        src.dependencies._client_cache = None
        await close_cached_temporal_client()
        allow_temporal_connects()

        fresh_client = AsyncMock(spec=Client)
        try:
            with patch(
                "src.dependencies.create_temporal_client", return_value=fresh_client
            ):
                client = await asyncio.wait_for(acquire_temporal_client(), timeout=1)

            assert client is fresh_client
            assert src.dependencies._client_cache is fresh_client
        finally:
            src.dependencies._client_cache = None

    @pytest.mark.asyncio
    async def test_warns_when_cancelled_connect_keeps_running(self) -> None:
        """A connect that ignores the cancel past cancel_wait_seconds is reported
        with a warning, and shutdown still returns."""
        import src.dependencies

        stop_ignoring = asyncio.Event()

        async def connect_ignoring_cancel() -> None:
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                await stop_ignoring.wait()

        connect_task = asyncio.create_task(connect_ignoring_cancel())
        await asyncio.sleep(0)  # let the task reach its first await
        src.dependencies._client_cache = None
        src.dependencies._connect_task = connect_task
        try:
            with patch("foundry_temporal.logging.get_logger") as mock_get_logger:
                await asyncio.wait_for(
                    close_cached_temporal_client(cancel_wait_seconds=0.05), timeout=1
                )

            warning = mock_get_logger.return_value.bind.return_value.warning
            warning.assert_called_once()
            assert "still running" in warning.call_args.args[0]
            assert not connect_task.done()
        finally:
            stop_ignoring.set()
            await asyncio.wait_for(connect_task, timeout=1)
            src.dependencies._connect_task = None

    @pytest.mark.asyncio
    async def test_app_shutdown_closes_cached_client(self) -> None:
        """The app's lifespan shutdown closes the cached client."""
        from fastapi import FastAPI

        from src.main import lifespan

        with (
            patch("src.main.allow_temporal_connects") as mock_allow,
            patch(
                "src.main.close_cached_temporal_client", new_callable=AsyncMock
            ) as mock_close,
        ):
            async with lifespan(FastAPI()):
                mock_allow.assert_called_once_with()
                mock_close.assert_not_awaited()
            mock_close.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_app_shutdown_closes_cached_client_when_lifespan_errors(
        self,
    ) -> None:
        """Shutdown cleanup still runs when an error is raised at the lifespan's
        yield."""
        from fastapi import FastAPI

        from src.main import lifespan

        with patch(
            "src.main.close_cached_temporal_client", new_callable=AsyncMock
        ) as mock_close:
            with pytest.raises(RuntimeError):
                async with lifespan(FastAPI()):
                    raise RuntimeError("server crashed")
            mock_close.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_app_shutdown_closes_cached_client_when_lifespan_cancelled(
        self,
    ) -> None:
        """Shutdown cleanup still runs when the lifespan task is cancelled, as on
        a forced server exit."""
        from fastapi import FastAPI

        from src.main import lifespan

        started = asyncio.Event()

        async def run_lifespan() -> None:
            async with lifespan(FastAPI()):
                started.set()
                await asyncio.sleep(5)

        with patch(
            "src.main.close_cached_temporal_client", new_callable=AsyncMock
        ) as mock_close:
            task = asyncio.create_task(run_lifespan())
            await asyncio.wait_for(started.wait(), timeout=1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=1)
            mock_close.assert_awaited_once_with()


class TestBuildTemporalClientConfig:
    """The one shared Temporal client config builder (V-222596)."""

    @pytest.mark.parametrize("mode", ["platform", "tls", "mtls"])
    def test_passes_configured_tls_mode(self, mode, tls_pem_files):
        config = AppConfig(
            temporal_host="test-temporal:7233",
            temporal_namespace="test-namespace",
            temporal_task_queue="test-queue",
            temporal_tls_mode=mode,
            temporal_tls_cert=tls_pem_files["cert"],
            temporal_tls_key=tls_pem_files["key"],
            _env_file=None,
        )

        worker_config = build_temporal_client_config(config)

        assert worker_config.tls_mode == mode
        assert worker_config.temporal_host == "test-temporal:7233"
        assert worker_config.temporal_namespace == "test-namespace"
        assert worker_config.task_queue == "test-queue"

    def test_defaults_to_app_config(self, tls_pem_files):
        config = AppConfig(
            temporal_tls_mode="mtls",
            temporal_tls_cert=tls_pem_files["cert"],
            temporal_tls_key=tls_pem_files["key"],
            _env_file=None,
        )

        with patch("src.dependencies.get_config", return_value=config):
            worker_config = build_temporal_client_config()

        assert worker_config.tls_mode == "mtls"

    def test_off_is_passed_through(self):
        """foundry-temporal accepts "off", so the builder sends it unchanged."""
        config = AppConfig(temporal_tls_mode="off", _env_file=None)

        assert build_temporal_client_config(config).tls_mode == "off"

    def test_forwards_tls_cert_key_and_ca(self, tls_pem_files):
        config = AppConfig(
            temporal_tls_mode="mtls",
            temporal_tls_cert=tls_pem_files["cert"],
            temporal_tls_key=tls_pem_files["key"],
            temporal_tls_ca=tls_pem_files["ca"],
            _env_file=None,
        )

        worker_config = build_temporal_client_config(config)

        assert worker_config.temporal_tls_cert == tls_pem_files["cert"]
        assert worker_config.temporal_tls_key == tls_pem_files["key"]
        assert worker_config.temporal_tls_ca == tls_pem_files["ca"]


class TestTemporalTlsRealConfigPath:
    """Runs foundry-temporal's real create_temporal_client so the PEM files must
    actually reach the TLS config. Only the final network connect is stubbed."""

    async def _connect_kwargs(self, config):
        from foundry_temporal.worker import create_temporal_client

        connect = AsyncMock(return_value=Mock())
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("foundry_temporal.worker.Client.connect", connect),
        ):
            await create_temporal_client(build_temporal_client_config(config))
        return connect.await_args.kwargs

    @pytest.mark.asyncio
    async def test_mtls_sends_client_cert_and_key(self, tls_pem_files):
        from temporalio.service import TLSConfig

        config = AppConfig(
            temporal_tls_mode="mtls",
            temporal_tls_cert=tls_pem_files["cert"],
            temporal_tls_key=tls_pem_files["key"],
            _env_file=None,
        )

        tls = (await self._connect_kwargs(config))["tls"]

        assert isinstance(tls, TLSConfig)
        assert tls.client_cert == b"-----client.pem-----"
        assert tls.client_private_key == b"-----client.key-----"

    @pytest.mark.asyncio
    async def test_tls_uses_private_ca(self, tls_pem_files):
        from temporalio.service import TLSConfig

        config = AppConfig(
            temporal_tls_mode="tls", temporal_tls_ca=tls_pem_files["ca"], _env_file=None
        )

        tls = (await self._connect_kwargs(config))["tls"]

        assert isinstance(tls, TLSConfig)
        assert tls.server_root_ca_cert == b"-----ca.pem-----"

    @pytest.mark.asyncio
    async def test_off_connects_without_application_tls(self):
        """The real create_temporal_client accepts "off" and connects without
        application-managed TLS."""
        config = AppConfig(temporal_tls_mode="off", _env_file=None)

        assert "tls" not in await self._connect_kwargs(config)


class TestRateLimiter:
    """Test RateLimiter class"""

    def test_rate_limiter_initialization(self):
        """Test RateLimiter initialization with default values"""
        limiter = RateLimiter()

        assert limiter.max_requests == 100
        assert limiter.window_seconds == 60
        assert limiter.clients == {}

    def test_rate_limiter_custom_initialization(self):
        """Test RateLimiter initialization with custom values"""
        limiter = RateLimiter(max_requests=50, window_seconds=30)

        assert limiter.max_requests == 50
        assert limiter.window_seconds == 30
        assert limiter.clients == {}

    @pytest.mark.asyncio
    async def test_rate_limiter_allows_requests_under_limit(self):
        """Test that requests under limit are allowed"""
        limiter = RateLimiter(max_requests=2, window_seconds=60)

        # First request should pass
        result1 = await limiter.check_rate_limit("client1")
        assert result1 is True

        # Second request should pass
        result2 = await limiter.check_rate_limit("client1")
        assert result2 is True

    @pytest.mark.asyncio
    async def test_rate_limiter_blocks_requests_over_limit(self):
        """Test that requests over limit are blocked"""
        limiter = RateLimiter(max_requests=2, window_seconds=60)

        # Fill up the limit
        await limiter.check_rate_limit("client1")
        await limiter.check_rate_limit("client1")

        # Third request should be blocked
        result = await limiter.check_rate_limit("client1")
        assert result is False

    @pytest.mark.asyncio
    async def test_rate_limiter_different_clients(self):
        """Test that different clients have separate limits"""
        limiter = RateLimiter(max_requests=1, window_seconds=60)

        # Client1 uses up their limit
        result1 = await limiter.check_rate_limit("client1")
        assert result1 is True

        result1_blocked = await limiter.check_rate_limit("client1")
        assert result1_blocked is False

        # Client2 should still be able to make requests
        result2 = await limiter.check_rate_limit("client2")
        assert result2 is True

    @pytest.mark.asyncio
    async def test_rate_limiter_window_cleanup(self):
        """Test that old requests are cleaned up"""
        limiter = RateLimiter(max_requests=2, window_seconds=1)  # 1 second window

        # Add requests to client
        await limiter.check_rate_limit("client1")
        await limiter.check_rate_limit("client1")

        # Client should be at limit
        result = await limiter.check_rate_limit("client1")
        assert result is False

        # Mock time to be past the window
        import time

        original_time = time.time
        with patch("time.time", return_value=original_time() + 2):  # 2 seconds later
            # Should be able to make request again
            result = await limiter.check_rate_limit("client1")
            assert result is True

    @pytest.mark.asyncio
    async def test_rate_limiter_tracks_request_times(self):
        """Test that rate limiter tracks request timestamps"""
        limiter = RateLimiter(max_requests=5, window_seconds=60)

        # Make a request
        await limiter.check_rate_limit("test_client")

        # Check that client is tracked
        assert "test_client" in limiter.clients
        assert len(limiter.clients["test_client"]) == 1

        # Make another request
        await limiter.check_rate_limit("test_client")

        # Check that both requests are tracked
        assert len(limiter.clients["test_client"]) == 2

    @pytest.mark.asyncio
    async def test_rate_limiter_empty_client_list(self):
        """Test rate limiter behavior with new client"""
        limiter = RateLimiter(max_requests=1, window_seconds=60)

        # New client should be allowed
        result = await limiter.check_rate_limit("new_client")
        assert result is True

        # Verify client was added to tracking
        assert "new_client" in limiter.clients
        assert len(limiter.clients["new_client"]) == 1

    @pytest.mark.asyncio
    async def test_is_over_limit_does_not_consume(self):
        """is_over_limit reports status without counting as a request."""
        limiter = RateLimiter(max_requests=2, window_seconds=60)

        assert limiter.is_over_limit("client1") is False

        await limiter.check_rate_limit("client1")
        await limiter.check_rate_limit("client1")

        assert limiter.is_over_limit("client1") is True
        # Checking again does not consume a slot or change the outcome
        assert limiter.is_over_limit("client1") is True
