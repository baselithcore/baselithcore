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


class TestServerRequestedRetryAfter:
    """A server that sent RFC 9110 ``Retry-After`` knows better than our
    backoff curve. Retrying before its window re-sends into a closed door and,
    with many providers, extends the throttle."""

    @pytest.mark.asyncio
    async def test_retry_after_overrides_the_backoff_curve(self, monkeypatch):
        import importlib

        retry_mod = importlib.import_module("core.resilience.retry")

        slept: list[float] = []

        async def _fake_sleep(seconds: float) -> None:
            slept.append(seconds)

        monkeypatch.setattr(retry_mod.asyncio, "sleep", _fake_sleep)

        class _Throttled(Exception):
            retry_after = 7.0

        calls = {"n": 0}

        @retry_mod.retry(max_attempts=2, base_delay=1.0, max_delay=60.0)
        async def _flaky():
            calls["n"] += 1
            if calls["n"] == 1:
                raise _Throttled("429")
            return "ok"

        assert await _flaky() == "ok"
        # The server's 7s wins over the 1s the curve would have chosen, and the
        # instruction is not jittered.
        assert slept == [7.0]

    @pytest.mark.asyncio
    async def test_retry_after_is_capped_by_max_delay(self, monkeypatch):
        import importlib

        retry_mod = importlib.import_module("core.resilience.retry")

        slept: list[float] = []

        async def _fake_sleep(seconds: float) -> None:
            slept.append(seconds)

        monkeypatch.setattr(retry_mod.asyncio, "sleep", _fake_sleep)

        class _Throttled(Exception):
            retry_after = 900.0  # absurd window

        calls = {"n": 0}

        @retry_mod.retry(max_attempts=2, base_delay=1.0, max_delay=30.0)
        async def _flaky():
            calls["n"] += 1
            if calls["n"] == 1:
                raise _Throttled("429")
            return "ok"

        await _flaky()
        assert slept == [30.0]

    @pytest.mark.asyncio
    async def test_absent_retry_after_keeps_the_backoff_curve(self, monkeypatch):
        import importlib

        retry_mod = importlib.import_module("core.resilience.retry")

        slept: list[float] = []

        async def _fake_sleep(seconds: float) -> None:
            slept.append(seconds)

        monkeypatch.setattr(retry_mod.asyncio, "sleep", _fake_sleep)
        calls = {"n": 0}

        @retry_mod.retry(max_attempts=2, base_delay=2.0, max_delay=30.0, jitter=False)
        async def _flaky():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            return "ok"

        await _flaky()
        assert slept == [2.0]

    def test_unusable_retry_after_values_are_ignored(self):
        from core.resilience.retry import _server_requested_delay

        class _E(Exception):
            pass

        for bad in (None, "not-a-number", -5, 0, True):
            exc = _E()
            exc.retry_after = bad
            assert _server_requested_delay(exc) is None


class TestParseRetryAfterHeader:
    """Reading the header off a provider SDK exception, duck-typed."""

    def _exc_with_header(self, value):
        from types import SimpleNamespace

        exc = Exception("429 rate limited")
        exc.response = SimpleNamespace(headers={"retry-after": value})
        return exc

    def test_reads_delta_seconds(self):
        from core.services.llm.service import _parse_retry_after

        assert _parse_retry_after(self._exc_with_header("12")) == 12.0

    def test_ignores_http_date_form(self):
        """The date form needs the server's clock; a skewed one would produce a
        wildly wrong wait, so we fall back to the backoff curve."""
        from core.services.llm.service import _parse_retry_after

        exc = self._exc_with_header("Wed, 21 Oct 2026 07:28:00 GMT")
        assert _parse_retry_after(exc) is None

    def test_ignores_absurdly_long_window(self):
        from core.services.llm.service import _parse_retry_after

        assert _parse_retry_after(self._exc_with_header("100000")) is None

    def test_exception_without_response_is_safe(self):
        from core.services.llm.service import _parse_retry_after

        assert _parse_retry_after(Exception("plain")) is None


class TestSyncPathHonoursRetryAfter:
    """The sync and async wrappers must agree.

    The delay computation lives in one shared helper precisely because keeping
    two copies is what previously let ``Retry-After`` support land on the async
    path only, leaving every sync caller on the blind backoff curve.
    """

    def test_sync_wrapper_honours_retry_after(self, monkeypatch):
        import importlib

        retry_mod = importlib.import_module("core.resilience.retry")
        slept: list[float] = []
        monkeypatch.setattr(retry_mod.time, "sleep", slept.append)

        class _Throttled(Exception):
            retry_after = 9.0

        calls = {"n": 0}

        @retry_mod.retry(max_attempts=2, base_delay=1.0, max_delay=60.0)
        def _flaky():
            calls["n"] += 1
            if calls["n"] == 1:
                raise _Throttled("429")
            return "ok"

        assert _flaky() == "ok"
        assert slept == [9.0]

    def test_sync_wrapper_falls_back_to_the_curve(self, monkeypatch):
        import importlib

        retry_mod = importlib.import_module("core.resilience.retry")
        slept: list[float] = []
        monkeypatch.setattr(retry_mod.time, "sleep", slept.append)
        calls = {"n": 0}

        @retry_mod.retry(max_attempts=2, base_delay=3.0, max_delay=60.0, jitter=False)
        def _flaky():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            return "ok"

        _flaky()
        assert slept == [3.0]
