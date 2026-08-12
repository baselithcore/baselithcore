"""Admin password verification: PBKDF2-SHA256 checking + a verified-credential
cache.

Split out of :mod:`core.middleware.security` so the security manager stays under
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

from core.observability.logging import get_logger

logger = get_logger(__name__)

# OWASP's current PBKDF2-SHA256 recommendation is 600k iterations; hashes below
# this floor are rejected outright rather than silently accepted, so a
# hand-rolled ``pbkdf2_sha256$1$...`` value can't masquerade as a real KDF.
PBKDF2_MIN_ITERATIONS = 100_000

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
    except Exception:
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

    derived = hashlib.pbkdf2_hmac("sha256", candidate.encode("utf-8"), salt, iterations)
    return secrets.compare_digest(derived, digest)


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
