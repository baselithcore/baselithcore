"""Unit tests for retry and timeout resilience helpers."""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core.resilience.retry import TimeoutError, retry, timeout


@pytest.fixture
def mock_resilience_config():
    return SimpleNamespace(
        retry_max_attempts=3,
        retry_base_delay=0.01,
        retry_max_delay=0.05,
        retry_exponential_base=2.0,
        retry_jitter=False,
    )


def test_retry_sync_succeeds_after_retry(mock_resilience_config):
    attempts = {"count": 0}

    with (
        patch(
            "core.resilience.retry.get_resilience_config",
            return_value=mock_resilience_config,
        ),
        patch("core.resilience.retry.time.sleep") as mock_sleep,
    ):

        @retry()
        def flaky() -> str:
            attempts["count"] += 1
            if attempts["count"] < 2:
                raise ValueError("temporary")
            return "ok"

        assert flaky() == "ok"
        assert attempts["count"] == 2
        mock_sleep.assert_called_once()


def test_retry_sync_exhausts_attempts(mock_resilience_config):
    with (
        patch(
            "core.resilience.retry.get_resilience_config",
            return_value=mock_resilience_config,
        ),
        patch("core.resilience.retry.time.sleep"),
    ):

        @retry(max_attempts=2, base_delay=0.0, max_delay=0.0, jitter=False)
        def always_fails() -> None:
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            always_fails()


def test_retry_sync_does_not_catch_non_retryable_exception(mock_resilience_config):
    with (
        patch(
            "core.resilience.retry.get_resilience_config",
            return_value=mock_resilience_config,
        ),
        patch("core.resilience.retry.time.sleep") as mock_sleep,
    ):

        @retry(retryable_exceptions=(ValueError,))
        def fail_with_type_error() -> None:
            raise TypeError("wrong type")

        with pytest.raises(TypeError, match="wrong type"):
            fail_with_type_error()

        mock_sleep.assert_not_called()


@pytest.mark.asyncio
async def test_retry_async_succeeds_after_retry(mock_resilience_config):
    attempts = {"count": 0}

    with (
        patch(
            "core.resilience.retry.get_resilience_config",
            return_value=mock_resilience_config,
        ),
        patch("core.resilience.retry.asyncio.sleep") as mock_sleep,
    ):

        @retry()
        async def flaky_async() -> str:
            attempts["count"] += 1
            if attempts["count"] < 2:
                raise ValueError("temporary")
            return "ok"

        assert await flaky_async() == "ok"
        assert attempts["count"] == 2
        mock_sleep.assert_called_once()


@pytest.mark.asyncio
async def test_timeout_allows_fast_async_function():
    @timeout(0.1)
    async def fast() -> str:
        await asyncio.sleep(0)
        return "done"

    assert await fast() == "done"


@pytest.mark.asyncio
async def test_timeout_raises_for_slow_async_function():
    @timeout(0.01)
    async def slow() -> None:
        await asyncio.sleep(0.05)

    with pytest.raises(TimeoutError, match="timed out"):
        await slow()


def test_timeout_rejects_sync_function():
    with pytest.raises(TypeError, match="only works with async functions"):

        @timeout(0.1)
        def sync_fn() -> None:
            return None
