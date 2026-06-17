"""
Chaos / fault-injection tests for the resilience primitives.

These inject failures (always-failing functions, transient flakiness, provider
outages, concurrency pressure) and assert the framework degrades gracefully:
circuits trip and fast-reject, fallback chains pick the next provider, retries
ride out transient errors, and bulkheads cap concurrency. They exercise the
real ``core/resilience`` and ``core/models/fallback`` code — no mocks of the
primitives themselves.

Run only these: ``pytest -m chaos``. Skip them: ``pytest -m "not chaos"``.
"""

import asyncio
import time

import pytest

from core.models.fallback import (
    AllProvidersFailedError,
    FallbackChain,
    Provider,
)
from core.resilience.bulkhead import Bulkhead
from core.resilience.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerError,
    CircuitState,
)
from core.resilience.retry import retry

pytestmark = pytest.mark.chaos


def _boom():
    raise RuntimeError("injected failure")


class TestCircuitBreaker:
    def test_trips_open_after_threshold(self):
        cb = CircuitBreaker("chaos-cb", fail_max=3, reset_timeout=60)
        for _ in range(3):
            with pytest.raises(RuntimeError):
                cb.call(_boom)
        assert cb.state == CircuitState.OPEN

    def test_open_circuit_fast_rejects(self):
        cb = CircuitBreaker("chaos-cb2", fail_max=1, reset_timeout=60)
        with pytest.raises(RuntimeError):
            cb.call(_boom)
        assert cb.state == CircuitState.OPEN
        # Now it rejects WITHOUT invoking the function (fast-fail).
        called = {"n": 0}

        def _tracked():
            called["n"] += 1
            return "ok"

        with pytest.raises(CircuitBreakerError):
            cb.call(_tracked)
        assert called["n"] == 0  # function never ran while OPEN

    def test_closed_circuit_passes_through(self):
        cb = CircuitBreaker("chaos-cb3", fail_max=3, reset_timeout=60)
        assert cb.call(lambda: "ok") == "ok"
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.slow
    def test_recovers_via_half_open(self):
        cb = CircuitBreaker("chaos-cb4", fail_max=1, reset_timeout=1, half_open_max=1)
        with pytest.raises(RuntimeError):
            cb.call(_boom)
        assert cb.state == CircuitState.OPEN
        time.sleep(1.1)  # let reset_timeout elapse → HALF_OPEN on next check
        # A successful trial call in HALF_OPEN closes the circuit.
        assert cb.call(lambda: "recovered") == "recovered"
        assert cb.state == CircuitState.CLOSED


class TestFallbackChain:
    @pytest.mark.asyncio
    async def test_falls_through_to_healthy_provider(self):
        async def primary():
            raise RuntimeError("primary down")

        async def secondary():
            return "from-secondary"

        chain = FallbackChain(
            [Provider("primary", primary), Provider("secondary", secondary)]
        )
        outcome = await chain.run()
        assert outcome.result == "from-secondary"
        # The attempt trail records the failed primary then the success.
        assert [a.succeeded for a in outcome.attempts] == [False, True]

    @pytest.mark.asyncio
    async def test_all_providers_fail_raises(self):
        async def down():
            raise RuntimeError("down")

        chain = FallbackChain([Provider("a", down), Provider("b", down)])
        with pytest.raises(AllProvidersFailedError):
            await chain.run()

    @pytest.mark.asyncio
    async def test_open_breaker_skips_provider(self):
        calls = {"primary": 0}

        async def primary():
            calls["primary"] += 1
            return "primary"

        async def secondary():
            return "secondary"

        chain = FallbackChain(
            [
                Provider("primary", primary, is_open=lambda: True),  # breaker OPEN
                Provider("secondary", secondary),
            ]
        )
        outcome = await chain.run()
        assert outcome.result == "secondary"
        assert calls["primary"] == 0  # skipped while its breaker is open


class TestRetry:
    @pytest.mark.asyncio
    async def test_rides_out_transient_failures(self):
        attempts = {"n": 0}

        @retry(max_attempts=3, base_delay=0.0)
        async def flaky():
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise RuntimeError("transient")
            return "ok"

        assert await flaky() == "ok"
        assert attempts["n"] == 3

    @pytest.mark.asyncio
    async def test_exhausts_then_raises(self):
        attempts = {"n": 0}

        @retry(max_attempts=2, base_delay=0.0)
        async def always_down():
            attempts["n"] += 1
            raise RuntimeError("down")

        with pytest.raises(RuntimeError):
            await always_down()
        assert attempts["n"] == 2


class TestBulkhead:
    @pytest.mark.asyncio
    async def test_caps_concurrency(self):
        bulkhead = Bulkhead(max_concurrent=2, name="chaos-bh")
        peak = {"value": 0}
        live = {"value": 0}

        @bulkhead
        async def work():
            live["value"] += 1
            peak["value"] = max(peak["value"], live["value"])
            await asyncio.sleep(0.02)
            live["value"] -= 1
            return True

        await asyncio.gather(*(work() for _ in range(10)))
        # Never more than the configured limit ran at once.
        assert peak["value"] <= 2
