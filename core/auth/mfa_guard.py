"""Replay and brute-force guards for TOTP verification.

RFC 6238 §5.2 requires the verifier to reject a second use of the same OTP:
an accepted code stays valid for the whole acceptance window (period ±
``valid_window`` steps, ~90 s at the defaults), so without tracking the
consumed counter an observed/phished code can be replayed after the victim
logged in. RFC 4226 §7.3 likewise requires throttling to keep a 6-digit space
from being brute-forced online.

:class:`InMemoryTOTPGuard` implements both concerns for a single process.
Multi-instance deployments should supply an implementation of the
:class:`TOTPGuard` protocol backed by shared storage (e.g. Redis ``SET NX`` on
``totp_used:{identity}:{counter}`` plus a fixed-window failure counter) so the
guarantees hold across replicas.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Protocol, runtime_checkable


@runtime_checkable
class TOTPGuard(Protocol):
    """Contract for TOTP replay/brute-force guards.

    All methods key on ``identity`` — an opaque stable identifier for the
    enrolled principal (user id, admin account name, …).
    """

    def allow_attempt(self, identity: str) -> bool:
        """Return ``False`` while the identity is locked out from attempts."""
        ...

    def record_failure(self, identity: str) -> None:
        """Register a failed code attempt for the identity."""
        ...

    def consume(self, identity: str, counter: int) -> bool:
        """Atomically consume a matched time-step counter.

        Returns ``False`` when the counter (or a later one) was already
        consumed — i.e. the presented code is a replay.
        """
        ...


class InMemoryTOTPGuard:
    """Single-process :class:`TOTPGuard` with LRU-bounded memory.

    Replay: keeps the highest accepted counter per identity and accepts only
    strictly greater counters — a re-presented (or older in-window) code is
    rejected even though its HMAC still matches.

    Brute force: a fixed window of failed attempts per identity; once
    ``max_failures`` accumulate within ``lockout_seconds``, further attempts
    are refused until the window expires. A successful verification clears
    the failure state.

    Not shared across processes — see the module docstring for multi-instance
    deployments.
    """

    def __init__(
        self,
        *,
        max_failures: int = 5,
        lockout_seconds: float = 300.0,
        max_identities: int = 10_000,
    ) -> None:
        if max_failures <= 0:
            raise ValueError("max_failures must be positive.")
        if lockout_seconds <= 0:
            raise ValueError("lockout_seconds must be positive.")
        if max_identities <= 0:
            raise ValueError("max_identities must be positive.")
        self._max_failures = max_failures
        self._lockout_seconds = lockout_seconds
        self._max_identities = max_identities
        self._lock = threading.Lock()
        # identity -> highest accepted counter (LRU-ordered for eviction).
        self._last_counter: OrderedDict[str, int] = OrderedDict()
        # identity -> (window_start_monotonic, failures_in_window).
        self._failures: OrderedDict[str, tuple[float, int]] = OrderedDict()

    def allow_attempt(self, identity: str) -> bool:
        """Return ``False`` while the identity is locked out."""
        now = time.monotonic()
        with self._lock:
            entry = self._failures.get(identity)
            if entry is None:
                return True
            window_start, failures = entry
            if now - window_start >= self._lockout_seconds:
                # Window elapsed — reset lazily.
                self._failures.pop(identity, None)
                return True
            return failures < self._max_failures

    def record_failure(self, identity: str) -> None:
        """Count a failed attempt inside the current fixed window."""
        now = time.monotonic()
        with self._lock:
            entry = self._failures.get(identity)
            if entry is None or now - entry[0] >= self._lockout_seconds:
                self._failures[identity] = (now, 1)
            else:
                self._failures[identity] = (entry[0], entry[1] + 1)
            self._failures.move_to_end(identity)
            while len(self._failures) > self._max_identities:
                self._failures.popitem(last=False)

    def consume(self, identity: str, counter: int) -> bool:
        """Accept ``counter`` only if strictly newer than the last accepted."""
        with self._lock:
            last = self._last_counter.get(identity)
            if last is not None and counter <= last:
                return False
            self._last_counter[identity] = counter
            self._last_counter.move_to_end(identity)
            while len(self._last_counter) > self._max_identities:
                self._last_counter.popitem(last=False)
            # Success clears the brute-force window.
            self._failures.pop(identity, None)
            return True


__all__ = ["InMemoryTOTPGuard", "TOTPGuard"]
