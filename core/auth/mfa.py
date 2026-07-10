"""Multi-factor authentication (TOTP / RFC 6238).

A dependency-free, standard-library implementation of time-based one-time
passwords (TOTP, :rfc:`6238`, built on HOTP :rfc:`4226`) plus single-use
recovery codes. Compatible with off-the-shelf authenticator apps (Google
Authenticator, Authy, 1Password, …) via the ``otpauth://`` provisioning URI.

This is the **primitive layer** for the second authentication factor mandated
by NIS2 Art. 21(2)(j). It is intentionally storage-agnostic: the framework
does not dictate a user store, so callers are responsible for persisting the
enrollment secret (encrypt it at rest with :mod:`core.security.encryption`) and
the recovery-code hashes, then invoking :meth:`TOTPProvider.verify_code` /
:meth:`TOTPProvider.verify_recovery_code` during the login step-up.

Design notes:
    * The shared secret is a high-entropy random value, so it is carried in
      :class:`pydantic.SecretStr` to keep it out of ``repr``/logs/Sentry frames.
    * Recovery codes are hashed with SHA-256 — like API keys (see
      :mod:`core.auth.api_keys`), they are random tokens, not human-chosen
      passwords, so a fast hash is correct and a slow KDF buys nothing.
    * All code comparisons use :func:`hmac.compare_digest` (constant-time) to
      avoid leaking timing information about a near-miss OTP.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import quote, urlencode

from pydantic import SecretStr

from core.auth.mfa_guard import InMemoryTOTPGuard, TOTPGuard

# Hash algorithms permitted in the OTP HMAC. SHA-1 is the de-facto default for
# authenticator apps; SHA-256/512 are offered for stricter deployments (the
# provisioning URI advertises the choice so the app stays in sync).
TOTPAlgorithm = Literal["sha1", "sha256", "sha512"]

_ALGORITHMS: dict[str, str] = {
    "sha1": "sha1",
    "sha256": "sha256",
    "sha512": "sha512",
}

# RFC 6238 / 4226 defaults shared by authenticator apps.
DEFAULT_PERIOD = 30
DEFAULT_DIGITS = 6
DEFAULT_ALGORITHM: TOTPAlgorithm = "sha1"
# 20 bytes = 160 bits — RFC 4226 §4 R6 recommends at least 128 bits and
# strongly recommends 160 for HMAC-SHA1.
DEFAULT_SECRET_BYTES = 20

_RECOVERY_CODE_BYTES = 10  # 80 bits of entropy per recovery code.


def generate_secret(num_bytes: int = DEFAULT_SECRET_BYTES) -> str:
    """Generate a fresh base32-encoded TOTP shared secret.

    Args:
        num_bytes: Entropy of the underlying random secret (default 20 bytes /
            160 bits). Values below 16 bytes are rejected.

    Returns:
        An unpadded, upper-case base32 string (the format authenticator apps
        expect in an ``otpauth://`` URI).
    """
    if num_bytes < 16:
        raise ValueError("TOTP secret must be at least 16 bytes (128 bits).")
    raw = secrets.token_bytes(num_bytes)
    return base64.b32encode(raw).decode("ascii").rstrip("=")


def _decode_secret(secret: str) -> bytes:
    """Decode a (possibly unpadded, mixed-case) base32 secret to raw bytes."""
    cleaned = secret.strip().replace(" ", "").upper()
    if not cleaned:
        raise ValueError("TOTP secret is empty.")
    # base64.b32decode requires the input length to be a multiple of 8.
    padding = (-len(cleaned)) % 8
    try:
        # binascii.Error (raised on bad base32) subclasses ValueError.
        return base64.b32decode(cleaned + ("=" * padding))
    except (ValueError, TypeError) as exc:
        raise ValueError("Invalid base32 TOTP secret.") from exc


def _hotp(key: bytes, counter: int, digits: int, algorithm: TOTPAlgorithm) -> str:
    """Compute an HOTP value (RFC 4226) for a single counter."""
    digest_name = _ALGORITHMS.get(algorithm)
    if digest_name is None:
        raise ValueError(f"Unsupported OTP algorithm: {algorithm!r}")
    msg = struct.pack(">Q", counter)
    mac = hmac.new(key, msg, digest_name).digest()
    # Dynamic truncation (RFC 4226 §5.3).
    offset = mac[-1] & 0x0F
    binary = (
        (mac[offset] & 0x7F) << 24
        | (mac[offset + 1] & 0xFF) << 16
        | (mac[offset + 2] & 0xFF) << 8
        | (mac[offset + 3] & 0xFF)
    )
    return str(binary % (10**digits)).zfill(digits)


def generate_totp(
    secret: str,
    *,
    timestamp: float | None = None,
    period: int = DEFAULT_PERIOD,
    digits: int = DEFAULT_DIGITS,
    algorithm: TOTPAlgorithm = DEFAULT_ALGORITHM,
) -> str:
    """Compute the TOTP code for ``secret`` at a given time (RFC 6238).

    Args:
        secret: Base32-encoded shared secret.
        timestamp: Unix time to compute for; defaults to "now".
        period: Time step in seconds (default 30).
        digits: Number of digits in the code (default 6).
        algorithm: HMAC hash to use (default ``sha1``).

    Returns:
        The zero-padded numeric OTP string.
    """
    if period <= 0:
        raise ValueError("TOTP period must be positive.")
    now = time.time() if timestamp is None else timestamp
    counter = int(now // period)
    return _hotp(_decode_secret(secret), counter, digits, algorithm)


def verify_totp(
    secret: str,
    code: str,
    *,
    timestamp: float | None = None,
    period: int = DEFAULT_PERIOD,
    digits: int = DEFAULT_DIGITS,
    algorithm: TOTPAlgorithm = DEFAULT_ALGORITHM,
    valid_window: int = 1,
) -> bool:
    """Verify a TOTP code, tolerating limited clock skew.

    Checks the current time step plus ``valid_window`` steps on either side so a
    small client/server clock drift (default ±30 s) does not reject a valid
    code. The comparison is constant-time.

    Args:
        secret: Base32-encoded shared secret.
        code: User-supplied OTP to validate.
        timestamp: Unix time to validate against; defaults to "now".
        period: Time step in seconds (must match enrollment).
        digits: Expected number of digits (must match enrollment).
        algorithm: HMAC hash (must match enrollment).
        valid_window: Adjacent time steps to also accept (default 1).

    Returns:
        ``True`` if the code matches any accepted time step, else ``False``.
    """
    return (
        verify_totp_matched_counter(
            secret,
            code,
            timestamp=timestamp,
            period=period,
            digits=digits,
            algorithm=algorithm,
            valid_window=valid_window,
        )
        is not None
    )


def verify_totp_matched_counter(
    secret: str,
    code: str,
    *,
    timestamp: float | None = None,
    period: int = DEFAULT_PERIOD,
    digits: int = DEFAULT_DIGITS,
    algorithm: TOTPAlgorithm = DEFAULT_ALGORITHM,
    valid_window: int = 1,
) -> int | None:
    """Like :func:`verify_totp`, but return the matched time-step counter.

    The counter is what a replay guard must record so an accepted code cannot
    be presented a second time within the acceptance window (RFC 6238 §5.2).

    Returns:
        The RFC 6238 counter the code matched, or ``None`` if it matched none.
    """
    candidate = (code or "").strip()
    if not candidate.isdigit() or len(candidate) != digits:
        return None
    if valid_window < 0:
        raise ValueError("valid_window must be non-negative.")
    now = time.time() if timestamp is None else timestamp
    key = _decode_secret(secret)
    counter = int(now // period)
    matched: int | None = None
    # Iterate the full window even after a hit so total work is independent of
    # where (or whether) the match occurs.
    for offset in range(-valid_window, valid_window + 1):
        expected = _hotp(key, counter + offset, digits, algorithm)
        if hmac.compare_digest(expected, candidate):
            matched = counter + offset
    return matched


def provisioning_uri(
    secret: str,
    account_name: str,
    issuer: str,
    *,
    period: int = DEFAULT_PERIOD,
    digits: int = DEFAULT_DIGITS,
    algorithm: TOTPAlgorithm = DEFAULT_ALGORITHM,
) -> str:
    """Build an ``otpauth://totp/…`` URI for QR-code enrollment.

    Follows the Key URI Format used by Google Authenticator and compatible apps.

    Args:
        secret: Base32-encoded shared secret.
        account_name: Identifier shown in the app (e.g. the user's email).
        issuer: Service name shown in the app (e.g. ``BaselithCore``).
        period: Time step in seconds.
        digits: Number of digits.
        algorithm: HMAC hash.

    Returns:
        The ``otpauth://`` URI string (encode this into a QR code client-side).
    """
    # Key URI label is "issuer:account"; keep the separator colon literal and
    # percent-encode each part (matches the otpauth spec examples).
    label = f"{quote(issuer, safe='')}:{quote(account_name, safe='')}"
    params = urlencode(
        {
            "secret": secret,
            "issuer": issuer,
            "algorithm": algorithm.upper(),
            "digits": digits,
            "period": period,
        }
    )
    return f"otpauth://totp/{label}?{params}"


def generate_recovery_codes(count: int = 10) -> list[str]:
    """Generate single-use recovery codes (plaintext — display once).

    Each code is a 16-character base32 token (~80 bits) formatted as two
    hyphen-separated groups for readability.

    Args:
        count: Number of codes to generate (default 10).

    Returns:
        A list of plaintext recovery codes. Persist only their hashes
        (:func:`hash_recovery_code`); show the plaintext to the user once.
    """
    if count <= 0:
        raise ValueError("count must be positive.")
    codes: list[str] = []
    for _ in range(count):
        token = (
            base64.b32encode(secrets.token_bytes(_RECOVERY_CODE_BYTES))
            .decode("ascii")
            .rstrip("=")
        )
        codes.append(f"{token[:8]}-{token[8:16]}")
    return codes


def _normalize_recovery_code(code: str) -> str:
    """Canonicalize a recovery code for hashing/comparison."""
    return (code or "").strip().upper().replace("-", "").replace(" ", "")


def hash_recovery_code(code: str) -> str:
    """Hash a recovery code (SHA-256) for storage at rest.

    Normalization makes the hash insensitive to the cosmetic hyphen and to
    case/whitespace so the user can re-type the code freely.
    """
    return hashlib.sha256(_normalize_recovery_code(code).encode()).hexdigest()


def verify_recovery_code(code: str, hashes: Sequence[str]) -> str | None:
    """Check a recovery code against stored hashes (constant-time).

    Args:
        code: User-supplied recovery code.
        hashes: The remaining unused recovery-code hashes for the identity.

    Returns:
        The matching hash (so the caller can mark it consumed) or ``None``.
        Recovery codes are single-use — the caller MUST remove the returned
        hash from the stored set after a successful login.
    """
    candidate = hash_recovery_code(code)
    matched: str | None = None
    for stored in hashes:
        if hmac.compare_digest(stored, candidate):
            matched = stored
    return matched


@dataclass
class MFAEnrollment:
    """Artifacts produced when a user enrolls a TOTP authenticator.

    The ``secret`` and ``recovery_code_hashes`` are persisted server-side (the
    secret encrypted at rest); ``recovery_codes`` is plaintext and shown to the
    user exactly once, so it is excluded from ``repr`` to avoid leaking into
    logs.
    """

    secret: SecretStr
    recovery_code_hashes: tuple[str, ...]
    account_name: str
    issuer: str
    period: int = DEFAULT_PERIOD
    digits: int = DEFAULT_DIGITS
    algorithm: TOTPAlgorithm = DEFAULT_ALGORITHM
    recovery_codes: tuple[str, ...] = field(default=(), repr=False)

    def provisioning_uri(self) -> str:
        """Return the ``otpauth://`` URI for QR enrollment (contains the secret)."""
        return provisioning_uri(
            self.secret.get_secret_value(),
            self.account_name,
            self.issuer,
            period=self.period,
            digits=self.digits,
            algorithm=self.algorithm,
        )


class TOTPProvider:
    """Config-bound facade over the TOTP/recovery-code primitives.

    Holds the deployment's issuer label and OTP parameters so call sites do not
    repeat them. Obtain the shared instance via
    :meth:`core.auth.manager.AuthManager.mfa`.
    """

    def __init__(
        self,
        issuer: str = "BaselithCore",
        *,
        period: int = DEFAULT_PERIOD,
        digits: int = DEFAULT_DIGITS,
        algorithm: TOTPAlgorithm = DEFAULT_ALGORITHM,
        valid_window: int = 1,
        recovery_code_count: int = 10,
        guard: TOTPGuard | None = None,
    ) -> None:
        self.issuer = issuer
        self.period = period
        self.digits = digits
        self.algorithm: TOTPAlgorithm = algorithm
        self.valid_window = valid_window
        self.recovery_code_count = recovery_code_count
        # Replay + brute-force guard (RFC 6238 §5.2 / RFC 4226 §7.3), engaged
        # whenever verify_code is given an ``identity``. The in-memory default
        # protects a single process; multi-instance deployments inject a
        # shared-storage implementation of the TOTPGuard protocol.
        self.guard: TOTPGuard = guard if guard is not None else InMemoryTOTPGuard()

    def enroll(self, account_name: str) -> MFAEnrollment:
        """Start an enrollment: mint a secret and a set of recovery codes."""
        secret = generate_secret()
        codes = generate_recovery_codes(self.recovery_code_count)
        return MFAEnrollment(
            secret=SecretStr(secret),
            recovery_codes=tuple(codes),
            recovery_code_hashes=tuple(hash_recovery_code(c) for c in codes),
            account_name=account_name,
            issuer=self.issuer,
            period=self.period,
            digits=self.digits,
            algorithm=self.algorithm,
        )

    def verify_code(
        self,
        secret: str | SecretStr,
        code: str,
        *,
        identity: str | None = None,
    ) -> bool:
        """Verify a TOTP ``code`` against the stored ``secret``.

        Args:
            secret: The enrolled base32 secret.
            code: User-supplied OTP.
            identity: Stable identifier of the principal being verified.
                When provided, the provider's :class:`TOTPGuard` enforces
                single-use codes (an accepted code cannot be replayed within
                the clock-skew window) and throttles failed attempts.
                **Pass it on every login step-up** — omitting it skips both
                protections and is only appropriate for stateless checks
                (e.g. verifying an enrollment confirmation code).

        Returns:
            ``True`` only for a fresh, within-window, non-replayed match.
        """
        raw = secret.get_secret_value() if isinstance(secret, SecretStr) else secret
        if identity is None:
            return verify_totp(
                raw,
                code,
                period=self.period,
                digits=self.digits,
                algorithm=self.algorithm,
                valid_window=self.valid_window,
            )
        if not self.guard.allow_attempt(identity):
            return False
        matched = verify_totp_matched_counter(
            raw,
            code,
            period=self.period,
            digits=self.digits,
            algorithm=self.algorithm,
            valid_window=self.valid_window,
        )
        if matched is None:
            self.guard.record_failure(identity)
            return False
        return self.guard.consume(identity, matched)

    def verify_recovery_code(self, code: str, hashes: Sequence[str]) -> str | None:
        """Verify a recovery ``code``; returns the consumed hash or ``None``."""
        return verify_recovery_code(code, hashes)

    def provisioning_uri(self, secret: str | SecretStr, account_name: str) -> str:
        """Build the ``otpauth://`` URI for an existing secret."""
        raw = secret.get_secret_value() if isinstance(secret, SecretStr) else secret
        return provisioning_uri(
            raw,
            account_name,
            self.issuer,
            period=self.period,
            digits=self.digits,
            algorithm=self.algorithm,
        )


__all__ = [
    "DEFAULT_ALGORITHM",
    "DEFAULT_DIGITS",
    "DEFAULT_PERIOD",
    "DEFAULT_SECRET_BYTES",
    "InMemoryTOTPGuard",
    "MFAEnrollment",
    "TOTPAlgorithm",
    "TOTPGuard",
    "TOTPProvider",
    "generate_recovery_codes",
    "generate_secret",
    "generate_totp",
    "hash_recovery_code",
    "provisioning_uri",
    "verify_recovery_code",
    "verify_totp",
    "verify_totp_matched_counter",
]
