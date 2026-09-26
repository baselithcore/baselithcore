"""Admin Basic-auth verification: lockout-serialized, KDF-bounded.

Extracted from :mod:`core.middleware.security` to keep that module under the
500-line cap. The single entry point, :func:`authenticate_admin_basic`, owns
the whole check → derive → record sequence so the lockout cannot be raced.

The previous shape (caller runs ``check_admin_lockout``, then the PBKDF2
verification, then ``record_admin_failure``) was check-then-act: a burst of
concurrent requests from one source all passed the up-front check while the
counter was still below the threshold, so 500 parallel requests bought 500
password guesses instead of five — and 500 PBKDF2 derivations at 600k
iterations, enough to saturate every core and the default executor.

Here every derivation runs inside a small per-process slot pool, and the
lockout is re-checked and the failure recorded *inside* the slot: whatever a
burst does, at most ``KDF_MAX_CONCURRENCY`` guesses are in flight past the
threshold, and the CPU cost of a spray is bounded to that many cores.
"""

from __future__ import annotations

import asyncio
import ipaddress
import secrets

#: Concurrent PBKDF2 derivations allowed per process (per event loop). Also
#: the bound on how many guesses a concurrent burst can land past the lockout
#: threshold. A legitimate admin burst never waits here: a credential verified
#: within the cache TTL skips the derivation entirely.
KDF_MAX_CONCURRENCY = 2

#: IPv6 prefix a single lockout / auth-failure budget covers. One subscriber
#: usually holds a whole /64, so keying on the full /128 hands an attacker
#: 2**64 fresh budgets by rotating the interface identifier.
IPV6_BUCKET_PREFIX = 64

_kdf_slot: tuple[asyncio.AbstractEventLoop, asyncio.Semaphore] | None = None


def client_bucket(ip: str) -> str:
    """Return the throttling key for a client address.

    IPv4 addresses (and anything unparsable, e.g. ``"unknown"``) are returned
    unchanged; IPv6 addresses collapse to their ``/64`` network, and
    IPv4-mapped IPv6 addresses to the embedded IPv4 address.

    Args:
        ip: The client address as reported by the ASGI server.

    Returns:
        The string to key per-source throttles on.
    """
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped is not None:
            return str(addr.ipv4_mapped)
        net = ipaddress.IPv6Network((addr, IPV6_BUCKET_PREFIX), strict=False)
        return str(net)
    return ip


def kdf_slot() -> asyncio.Semaphore:
    """The KDF slot pool for the running event loop.

    Rebuilt when the loop changes (test suites run many loops per process;
    a semaphore bound to a closed loop would raise on first contention).
    """
    global _kdf_slot
    loop = asyncio.get_running_loop()
    if _kdf_slot is None or _kdf_slot[0] is not loop:
        _kdf_slot = (loop, asyncio.Semaphore(KDF_MAX_CONCURRENCY))
    return _kdf_slot[1]


async def authenticate_admin_basic(
    client_ip: str, username: str, password: str
) -> bool:
    """Verify admin Basic-auth credentials under the per-source lockout.

    Raises the lockout's ``429`` (or its fail-closed ``503``) when the source
    is locked out, before or after waiting for a KDF slot. A failure is
    recorded against the source; a success clears its counter.

    Args:
        client_ip: The caller's address; bucketed with :func:`client_bucket`.
        username: The presented username.
        password: The presented password.

    Returns:
        ``True`` when both username and password match the configured admin.
    """
    from core.middleware.security import get_security_manager

    manager = get_security_manager()
    identifier = client_bucket(client_ip)
    await manager.check_admin_lockout(identifier)

    # Bytes, not str: ``compare_digest`` raises TypeError on non-ASCII str,
    # which turned a UTF-8 username into a 500.
    user_ok = secrets.compare_digest(
        username.encode("utf-8"), manager.config.admin_user.encode("utf-8")
    )
    if user_ok and manager.admin_credential_cached(password):
        await manager.clear_admin_failures(identifier)
        return True

    async with kdf_slot():
        # Failures recorded while this request queued for a slot count now.
        await manager.check_admin_lockout(identifier)
        # The derivation runs even when the username is wrong, so the response
        # time does not reveal which half of the credential was right.
        password_ok = await asyncio.to_thread(manager.verify_admin_password, password)
        if not (user_ok and password_ok):
            # Recorded before the slot is released, so the next queued
            # request's re-check above already sees it.
            await manager.record_admin_failure(identifier)
            return False

    await manager.clear_admin_failures(identifier)
    return True


__all__ = [
    "IPV6_BUCKET_PREFIX",
    "KDF_MAX_CONCURRENCY",
    "authenticate_admin_basic",
    "client_bucket",
    "kdf_slot",
]
