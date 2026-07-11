"""TOTP replay guard + brute-force throttle (RFC 6238 §5.2 / RFC 4226 §7.3)."""

import time

import pytest

from core.auth.mfa import (
    InMemoryTOTPGuard,
    TOTPGuard,
    TOTPProvider,
    generate_secret,
    generate_totp,
    verify_totp_matched_counter,
)


@pytest.fixture
def provider():
    return TOTPProvider()


@pytest.fixture
def secret():
    return generate_secret()


class TestMatchedCounter:
    def test_returns_counter_for_valid_code(self, secret):
        now = time.time()
        code = generate_totp(secret, timestamp=now)
        counter = verify_totp_matched_counter(secret, code, timestamp=now)
        assert counter == int(now // 30)

    def test_returns_none_for_bad_code(self, secret):
        assert verify_totp_matched_counter(secret, "000000") in (None,)

    def test_adjacent_window_returns_that_counter(self, secret):
        now = time.time()
        prev_code = generate_totp(secret, timestamp=now - 30)
        counter = verify_totp_matched_counter(secret, prev_code, timestamp=now)
        assert counter == int((now - 30) // 30)


class TestReplayGuard:
    def test_first_use_accepted_replay_rejected(self, provider, secret):
        code = generate_totp(secret)
        assert provider.verify_code(secret, code, identity="alice") is True
        # Same still-in-window code again: replay — rejected.
        assert provider.verify_code(secret, code, identity="alice") is False

    def test_identities_are_independent(self, provider, secret):
        code = generate_totp(secret)
        assert provider.verify_code(secret, code, identity="alice") is True
        assert provider.verify_code(secret, code, identity="bob") is True

    def test_without_identity_behaviour_is_stateless(self, provider, secret):
        """No identity → legacy pure verification (documented opt-out)."""
        code = generate_totp(secret)
        assert provider.verify_code(secret, code) is True
        assert provider.verify_code(secret, code) is True

    def test_older_counter_rejected_after_newer_consumed(self, secret):
        guard = InMemoryTOTPGuard()
        assert guard.consume("u", 100) is True
        assert guard.consume("u", 99) is False  # older in-window code
        assert guard.consume("u", 100) is False  # exact replay
        assert guard.consume("u", 101) is True  # next step fine


class TestBruteForceThrottle:
    def test_lockout_after_max_failures(self, secret):
        provider = TOTPProvider(guard=InMemoryTOTPGuard(max_failures=3))
        for _ in range(3):
            assert provider.verify_code(secret, "000000", identity="carol") is False
        # Locked out: even the CORRECT code is refused while the window holds.
        good = generate_totp(secret)
        assert provider.verify_code(secret, good, identity="carol") is False

    def test_success_resets_failure_window(self, secret):
        provider = TOTPProvider(guard=InMemoryTOTPGuard(max_failures=3))
        provider.verify_code(secret, "000000", identity="dave")
        provider.verify_code(secret, "000000", identity="dave")
        good = generate_totp(secret)
        assert provider.verify_code(secret, good, identity="dave") is True
        # Window cleared: two more failures do not lock.
        provider.verify_code(secret, "000000", identity="dave")
        provider.verify_code(secret, "000000", identity="dave")
        assert provider.guard.allow_attempt("dave") is True

    def test_window_expiry_unlocks(self, secret):
        guard = InMemoryTOTPGuard(max_failures=1, lockout_seconds=0.05)
        provider = TOTPProvider(guard=guard)
        provider.verify_code(secret, "000000", identity="erin")
        assert guard.allow_attempt("erin") is False
        time.sleep(0.06)
        assert guard.allow_attempt("erin") is True


class TestGuardHygiene:
    def test_protocol_conformance(self):
        assert isinstance(InMemoryTOTPGuard(), TOTPGuard)

    def test_memory_bounded_lru(self):
        guard = InMemoryTOTPGuard(max_identities=3)
        for i in range(5):
            guard.consume(f"u{i}", 1)
        assert len(guard._last_counter) == 3
        # Oldest identities evicted first.
        assert "u0" not in guard._last_counter
        assert "u4" in guard._last_counter

    def test_invalid_params_rejected(self):
        with pytest.raises(ValueError):
            InMemoryTOTPGuard(max_failures=0)
        with pytest.raises(ValueError):
            InMemoryTOTPGuard(lockout_seconds=0)
        with pytest.raises(ValueError):
            InMemoryTOTPGuard(max_identities=0)
