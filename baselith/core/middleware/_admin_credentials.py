"""Admin password verification: Argon2id / PBKDF2-SHA256 checking + a
verified-credential cache.

Split out of :mod:`baselith.core.middleware.security` so the security manager stays under
the file-size cap. The cache lets a burst of identical Basic-auth requests
(Prometheus scrapes, admin dashboard polls) skip the 100k+ iteration PBKDF2
derivation: only *successful* verifications are memoized, keyed by a per-process
HMAC of the candidate (never the raw password, never a plain unsalted hash), so
a wrong guess can never populate the cache and turn into an accepted login.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from baselith.core.observability.logging import get_logger

logger = get_logger(__name__)

# OWASP's current PBKDF2-SHA256 recommendation is 600k iterations; hashes below
# this floor are rejected outright rather than silently accepted, so a
# hand-rolled ``pbkdf2_sha256$1$...`` value can't masquerade as a real KDF.
# Hashes between the floor and the recommendation verify but log a warning —
# a silently accepted 100k hash would stay under-hardened forever.
PBKDF2_MIN_ITERATIONS = 100_000
PBKDF2_RECOMMENDED_ITERATIONS = 600_000

# A verified credential stays cached for this many seconds. Short enough to
# bound exposure yet long enough to absorb scrape/poll bursts; the admin hash is
# static per process (env-loaded at startup), so there is no mid-process
# rotation to invalidate against.
CRED_CACHE_TTL_SECONDS = 300.0


def verify_pbkdf2_sha256(encoded: str, candidate: str) -> bool:
    """Verify a PBKDF2-SHA256 hash (rejecting under-iterated hashes)."""
    try:
        scheme, iter_str, salt_hex, hash_hex = encoded.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        iterations = int(iter_str)
        salt = bytes.fromhex(salt_hex)
        digest = bytes.fromhex(hash_hex)
    except (ValueError, TypeError, AttributeError):  # malformed encoded hash
        return False

    if iterations < PBKDF2_MIN_ITERATIONS:
        logger.error(
            "ADMIN_PASS_HASHED uses %d PBKDF2 iterations (< %d floor) — "
            "rejected. Regenerate with >=600000 iterations, e.g.: "
            'python -c "import hashlib,os;s=os.urandom(16);'
            "print('pbkdf2_sha256$600000$'+s.hex()+'$'+hashlib.pbkdf2_hmac("
            "'sha256',b'<password>',s,600000).hex())\"",
            iterations,
            PBKDF2_MIN_ITERATIONS,
        )
        return False

    if iterations < PBKDF2_RECOMMENDED_ITERATIONS:
        logger.warning(
            "ADMIN_PASS_HASHED uses %d PBKDF2 iterations — above the %d floor "
            "but below the recommended %d. Regenerate the hash with >=%d "
            "iterations at the next rotation.",
            iterations,
            PBKDF2_MIN_ITERATIONS,
            PBKDF2_RECOMMENDED_ITERATIONS,
            PBKDF2_RECOMMENDED_ITERATIONS,
        )

    derived = hashlib.pbkdf2_hmac("sha256", candidate.encode("utf-8"), salt, iterations)
    return secrets.compare_digest(derived, digest)


# OWASP Password Storage Cheat Sheet, minimum Argon2id profile: 19 MiB of
# memory, 2 iterations, 1 lane. A hash tuned below it is rejected outright for
# the same reason a 1-iteration PBKDF2 value is: it would only look like a KDF.
ARGON2_MIN_MEMORY_KIB = 19_456
ARGON2_MIN_TIME_COST = 2
ARGON2_MIN_PARALLELISM = 1


def verify_argon2id(encoded: str, candidate: str) -> bool:
    """Verify an Argon2id hash in the standard ``$argon2id$v=19$m=..,t=..,p=..$`` form.

    ``argon2-cffi`` is imported here, not at module scope, so a deployment that
    keeps a PBKDF2 hash never pays for it. Hashes tuned below the OWASP minimum
    (:data:`ARGON2_MIN_MEMORY_KIB`, :data:`ARGON2_MIN_TIME_COST`) are rejected.
    """
    from argon2 import PasswordHasher, extract_parameters
    from argon2.exceptions import InvalidHashError, VerificationError

    try:
        params = extract_parameters(encoded)
    except (InvalidHashError, ValueError):
        return False
    if (
        params.memory_cost < ARGON2_MIN_MEMORY_KIB
        or params.time_cost < ARGON2_MIN_TIME_COST
        or params.parallelism < ARGON2_MIN_PARALLELISM
    ):
        logger.error(
            "ADMIN_PASS_HASHED is an Argon2id hash with m=%d KiB, t=%d, p=%d — "
            "below the OWASP minimum (m>=%d KiB, t>=%d); rejected. Regenerate "
            "it with argon2-cffi's PasswordHasher().hash(password).",
            params.memory_cost,
            params.time_cost,
            params.parallelism,
            ARGON2_MIN_MEMORY_KIB,
            ARGON2_MIN_TIME_COST,
        )
        return False
    try:
        return bool(PasswordHasher().verify(encoded, candidate))
    except (VerificationError, InvalidHashError):
        return False


def verify_admin_hash(encoded: str, candidate: str) -> bool:
    """Verify ``candidate`` against an ``ADMIN_PASS_HASHED`` value of either scheme.

    ``$argon2id$...`` is Argon2id; ``pbkdf2_sha256$...`` stays supported so an
    existing deployment keeps working unchanged. Anything else is refused.
    """
    if encoded.startswith("$argon2id$"):
        return verify_argon2id(encoded, candidate)
    return verify_pbkdf2_sha256(encoded, candidate)


class VerifiedCredentialCache:
    """Per-process TTL cache of successfully verified admin credentials.

    Keys are an HMAC of the candidate under a random per-process key, so the
    stored value is neither the password nor a rainbow-reversible hash.
    """

    def __init__(self, ttl_seconds: float = CRED_CACHE_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._entries: dict[str, float] = {}
        self._key = secrets.token_bytes(32)

    @property
    def entries(self) -> dict[str, float]:
        """The live token -> expiry map (exposed for tests/introspection)."""
        return self._entries

    def token(self, candidate: str) -> str:
        """Per-process HMAC of a candidate — the cache key (never the password)."""
        return hmac.new(
            self._key, candidate.encode("utf-8"), hashlib.sha256
        ).hexdigest()

    def is_fresh(self, candidate: str) -> bool:
        """Whether ``candidate`` was verified within the TTL (evicts if stale)."""
        expiry = self._entries.get(self.token(candidate))
        if expiry is None:
            return False
        if expiry > time.time():
            return True
        self._entries.pop(self.token(candidate), None)
        return False

    def remember(self, candidate: str) -> None:
        """Record ``candidate`` as verified, with a TTL, bounding cache size."""
        now = time.time()
        # Opportunistic sweep of expired entries; keeps the map from growing
        # under credential-spraying (only successes are stored, so this is tiny
        # in practice, but the guard is cheap insurance).
        if len(self._entries) > 1000:
            self._entries = {k: exp for k, exp in self._entries.items() if exp > now}
        self._entries[self.token(candidate)] = now + self._ttl
