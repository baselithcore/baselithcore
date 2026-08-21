"""Property-based tests (hypothesis) for core.resilience invariants.

The resilience layer is the strict-typed, highest-assurance zone; these
properties pin down behavior that example-based tests only sample:
backoff bounds/monotonicity and the circuit-breaker state machine.
"""

from unittest.mock import patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from core.resilience.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerError,
    CircuitState,
)
from core.resilience.retry import retry

pytestmark = pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")


class _Boom(Exception):
    pass


@settings(max_examples=50, deadline=None)
@given(
    max_attempts=st.integers(min_value=2, max_value=6),
    base_delay=st.floats(min_value=0.001, max_value=2.0),
    max_delay=st.floats(min_value=0.001, max_value=10.0),
    exponential_base=st.floats(min_value=1.0, max_value=4.0),
)
def test_backoff_delays_bounded_and_monotone(
    max_attempts, base_delay, max_delay, exponential_base
):
    """Without jitter: delay_i = min(base * exp^i, max), non-decreasing, <= max."""
    sleeps: list[float] = []

    @retry(
        max_attempts=max_attempts,
        base_delay=base_delay,
        max_delay=max_delay,
        exponential_base=exponential_base,
        jitter=False,
        retryable_exceptions=(_Boom,),
    )
    def always_fails():
        raise _Boom()

    with patch("core.resilience.retry.time.sleep", side_effect=sleeps.append):
        with pytest.raises(_Boom):
            always_fails()

    assert len(sleeps) == max_attempts - 1
    for i, delay in enumerate(sleeps):
        expected = min(base_delay * (exponential_base**i), max_delay)
        assert delay == pytest.approx(expected)
        assert delay <= max_delay + 1e-9
    assert sleeps == sorted(sleeps)


@settings(max_examples=50, deadline=None)
@given(
    base_delay=st.floats(min_value=0.001, max_value=2.0),
    max_delay=st.floats(min_value=0.001, max_value=10.0),
)
def test_backoff_jitter_stays_within_band(base_delay, max_delay):
    """With jitter: every delay lies in [0.5, 1.5) x the nominal delay."""
    sleeps: list[float] = []

    @retry(
        max_attempts=4,
        base_delay=base_delay,
        max_delay=max_delay,
        exponential_base=2.0,
        jitter=True,
        retryable_exceptions=(_Boom,),
    )
    def always_fails():
        raise _Boom()

    with patch("core.resilience.retry.time.sleep", side_effect=sleeps.append):
        with pytest.raises(_Boom):
            always_fails()

    for i, delay in enumerate(sleeps):
        nominal = min(base_delay * (2.0**i), max_delay)
        assert 0.5 * nominal - 1e-9 <= delay < 1.5 * nominal + 1e-9


@settings(max_examples=100, deadline=None)
@given(
    outcomes=st.lists(st.booleans(), min_size=1, max_size=30),
    fail_max=st.integers(min_value=1, max_value=5),
)
def test_circuit_breaker_state_machine_invariants(outcomes, fail_max):
    """Drive a breaker with an arbitrary success/failure sequence and check:

    - CLOSED opens exactly when consecutive failures reach fail_max;
    - while OPEN (before reset_timeout) every call is rejected without
      executing the wrapped callable;
    - a success in CLOSED resets the consecutive-failure streak.
    """
    breaker = CircuitBreaker(
        name="pbt", fail_max=fail_max, reset_timeout=10_000, half_open_max=1
    )
    streak = 0
    executed: list[bool] = []

    def make_call(success: bool):
        def fn():
            executed.append(success)
            if not success:
                raise _Boom()
            return "ok"

        return fn

    for success in outcomes:
        if breaker.state == CircuitState.OPEN:
            before = len(executed)
            with pytest.raises(CircuitBreakerError):
                breaker.call(make_call(success))
            # Open circuit must short-circuit: the callable never ran.
            assert len(executed) == before
            continue

        if success:
            assert breaker.call(make_call(True)) == "ok"
            streak = 0
            assert breaker.state == CircuitState.CLOSED
        else:
            with pytest.raises(_Boom):
                breaker.call(make_call(False))
            streak += 1
            expected_open = streak >= fail_max
            assert (breaker.state == CircuitState.OPEN) == expected_open
