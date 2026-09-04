"""
A2A request signing.

Optional HMAC-SHA256 authentication for agent-to-agent HTTP traffic.

When the environment variable ``BASELITH_A2A_SHARED_SECRET`` is set, the
:class:`~core.a2a.client.A2AClient` signs every outgoing request body and the
A2A router rejects incoming requests whose signature is missing or invalid.
Without the secret the protocol behaves exactly as before (unauthenticated),
preserving backward compatibility for single-process and trusted-mesh
deployments — but a CRITICAL log is emitted in production so the posture is
never silent.

Wire format (HTTP headers):

- ``X-A2A-Timestamp``: unix epoch seconds at signing time.
- ``X-A2A-Nonce``: single-use random token, bound into the MAC.
- ``X-A2A-Signature``: ``sha256=<hex>`` where ``hex`` is
  ``HMAC_SHA256(secret, timestamp + "." + nonce + "." + raw_body)`` when a
  nonce is present, or the legacy ``HMAC_SHA256(secret, timestamp + "." +
  raw_body)`` when it is not.

The timestamp bounds replay to the skew window; the nonce closes it entirely —
the verifier records each nonce for the window's duration and rejects repeats,
so a captured signed request cannot be re-sent even inside the window. The
nonce cannot be stripped for a downgrade: it is bound inside the MAC, so
removing the header invalidates the signature. The nonce is REQUIRED by
default: a nonce-less request, even with a valid legacy MAC, is replayable for
the whole skew window, so it is refused unless the operator explicitly opts
into the deprecated compatibility window with
``BASELITH_A2A_ALLOW_LEGACY_NONCELESS=true`` while older peers are upgraded.
The nonce ledger is Redis-backed (``SET NX EX``) when the deployment's cache
backend is Redis, giving cross-replica single-use; otherwise it is
per-process, where the residual exposure is one skew window per replica. A
Redis outage degrades to the per-process ledger (fail towards the documented
per-replica posture, never towards accepting replays outright) with a warning.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import time
import uuid

from pydantic import SecretStr

from core.observability.logging import get_logger
from core.utils.logsafe import sanitize_log_value

logger = get_logger(__name__)

TIMESTAMP_HEADER = "X-A2A-Timestamp"
NONCE_HEADER = "X-A2A-Nonce"
SIGNATURE_HEADER = "X-A2A-Signature"
PEER_HEADER = "X-A2A-Peer"
_SIGNATURE_PREFIX = "sha256="

# Peer ids travel inside the MAC message with "." as the field separator, so
# they must not contain one (framing ambiguity); keep them short and plain.
_PEER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

#: Maximum accepted clock skew between signer and verifier, in seconds.
DEFAULT_MAX_SKEW_SECONDS = 300


class _NonceLedger:
    """Process-local single-use ledger with lazy TTL pruning."""

    def __init__(self) -> None:
        self._seen: dict[str, float] = {}

    def register_once(self, nonce: str, ttl_seconds: float) -> bool:
        """Record ``nonce``; False when it was already seen (replay)."""
        now = time.time()
        # Lazy prune: the ledger is bounded by the request rate × window.
        if len(self._seen) > 10_000:
            self._seen = {n: exp for n, exp in self._seen.items() if exp > now}
        if self._seen.get(nonce, 0.0) > now:
            return False
        self._seen[nonce] = now + ttl_seconds
        return True


_NONCE_KEY_PREFIX = "baselith:a2a:nonce:"


class _RedisNonceLedger:
    """Cross-replica single-use ledger backed by Redis ``SET NX EX``.

    Falls back to the process-local ledger for a call whose Redis round-trip
    fails: the per-replica posture is the documented degradation, and A2A
    availability must not hinge on the cache tier.
    """

    def __init__(self, client: object, fallback: _NonceLedger) -> None:
        self._client = client
        self._fallback = fallback
        self._warned = False

    def register_once(self, nonce: str, ttl_seconds: float) -> bool:
        try:
            created = self._client.set(  # type: ignore[attr-defined]
                f"{_NONCE_KEY_PREFIX}{nonce}",
                b"1",
                nx=True,
                ex=max(1, int(ttl_seconds) + 1),
            )
        except Exception as exc:
            if not self._warned:
                logger.warning(
                    "A2A nonce ledger: Redis unavailable (%s); degrading to "
                    "the per-process ledger (single-use per replica only).",
                    type(exc).__name__,
                )
                self._warned = True
            return self._fallback.register_once(nonce, ttl_seconds)
        # redis-py: True on a successful NX write, None when the key existed.
        return bool(created)


def _build_nonce_ledger() -> _NonceLedger | _RedisNonceLedger:
    """Pick the strongest nonce ledger the deployment supports.

    Same selection rule as the AP2 replay guard: Redis only when the cache
    backend is genuinely Redis (URL alone would match the stock default with
    no Redis deployed).
    """
    fallback = _NonceLedger()
    try:
        from core.config import get_storage_config

        storage = get_storage_config()
        if getattr(storage, "cache_backend", "") != "redis":
            return fallback
        redis_url = getattr(storage, "cache_redis_url", "") or ""
        if not redis_url:
            return fallback
        from core.cache.redis_sync import create_sync_redis_client

        return _RedisNonceLedger(create_sync_redis_client(redis_url), fallback=fallback)
    except Exception:  # pragma: no cover - config/redis unavailable
        return fallback


_nonce_ledger: _NonceLedger | _RedisNonceLedger | None = None


def _get_nonce_ledger() -> _NonceLedger | _RedisNonceLedger:
    """Lazily build (and memoize) the process-wide nonce ledger."""
    global _nonce_ledger
    if _nonce_ledger is None:
        _nonce_ledger = _build_nonce_ledger()
    return _nonce_ledger


_ENV_SECRET = "BASELITH_A2A_SHARED_SECRET"  # noqa: S105
_ENV_ALLOW_UNAUTH = "BASELITH_A2A_ALLOW_UNAUTHENTICATED"
_ENV_ALLOW_LEGACY_NONCELESS = "BASELITH_A2A_ALLOW_LEGACY_NONCELESS"
_ENV_PEER_ID = "BASELITH_A2A_PEER_ID"
_ENV_PEER_SECRETS = "BASELITH_A2A_PEER_SECRETS"
_warned_unauthenticated = False
_warned_legacy_nonceless = False


def legacy_nonceless_allowed() -> bool:
    """Whether a signed request without a nonce may still verify.

    Deprecated compatibility window for peers predating the nonce: their MAC is
    valid but replayable for the whole skew window, so acceptance requires an
    explicit operator opt-in. Enabling it logs CRITICAL once so the weakened
    posture is never silent.
    """
    raw = os.environ.get(_ENV_ALLOW_LEGACY_NONCELESS, "").strip().lower()
    allowed = raw in ("1", "true", "yes", "on")
    global _warned_legacy_nonceless
    if allowed and not _warned_legacy_nonceless:
        logger.critical(
            "A2A accepts DEPRECATED nonce-less signatures "
            "(BASELITH_A2A_ALLOW_LEGACY_NONCELESS=true): captured requests are "
            "replayable within the skew window. Upgrade all peers and remove "
            "the opt-in."
        )
        _warned_legacy_nonceless = True
    return allowed


def get_a2a_shared_secret() -> SecretStr | None:
    """Return the configured A2A shared secret, or None when not set."""
    raw = os.environ.get(_ENV_SECRET, "").strip()
    return SecretStr(raw) if raw else None


def get_a2a_peer_id() -> str | None:
    """This instance's peer identity for outgoing requests, or None.

    Set ``BASELITH_A2A_PEER_ID`` to sign outgoing requests as a named peer
    (the id is bound inside the MAC). Invalid ids are ignored with a warning
    rather than producing an unverifiable frame.
    """
    raw = os.environ.get(_ENV_PEER_ID, "").strip()
    if not raw:
        return None
    if not _PEER_ID_RE.match(raw):
        logger.warning(
            "Ignoring invalid %s=%r (allowed: [A-Za-z0-9_-]{1,64})",
            _ENV_PEER_ID,
            raw[:80],
        )
        return None
    return raw


def get_a2a_peer_secrets() -> dict[str, SecretStr]:
    """Per-peer verification secrets from ``BASELITH_A2A_PEER_SECRETS``.

    Format: ``peerA=secretA,peerB=secretB``. With ONE mesh-wide secret every
    peer can both mint and verify, so any compromised peer impersonates all
    others; per-peer secrets shrink the blast radius of one leaked secret to
    that single identity. (True non-repudiation needs asymmetric signatures —
    out of scope for the HMAC transport.) Malformed entries are skipped with
    a warning rather than aborting startup.
    """
    raw = os.environ.get(_ENV_PEER_SECRETS, "").strip()
    if not raw:
        return {}
    out: dict[str, SecretStr] = {}
    for position, entry in enumerate(raw.split(","), start=1):
        entry = entry.strip()
        if not entry:
            continue
        peer, sep, material = entry.partition("=")
        peer = peer.strip()
        # The two event names below deliberately omit "secret": neither logs
        # any, and the word alone trips credential-disclosure scanners on a
        # parser that must stay auditable. Keep them as they are.
        if not sep or not peer or not material.strip():
            # Position only, never content: with no separator ``partition``
            # puts the WHOLE entry in ``peer``, so an operator who set the
            # variable to a bare secret would have it partially disclosed
            # here. The ordinal still points at the entry to fix.
            logger.warning("a2a_peer_entry_malformed position=%d", position)
            continue
        if not _PEER_ID_RE.match(peer):
            # Left of the separator: an identifier, never secret material.
            # Sanitized because it is unvalidated configuration input.
            logger.warning(
                "a2a_peer_invalid_peer_id peer=%s",
                sanitize_log_value(peer[:16]),
            )
            continue
        out[peer] = SecretStr(material.strip())
    return out


def _is_production() -> bool:
    """Whether the runtime environment is production.

    Shares :mod:`core.utils.runtime_env` with the plugin integrity gate: the
    local copy this replaced accepted the literal ``"production"`` only, so
    ``APP_ENV=prod`` left unsigned A2A requests allowed.
    """
    from core.utils.runtime_env import is_production_env

    return is_production_env()


def unauthenticated_a2a_allowed() -> bool:
    """Whether an unsigned A2A request may be processed.

    Fail-closed in production: when no shared secret is configured, unsigned
    requests are refused unless the operator explicitly opts in with
    ``BASELITH_A2A_ALLOW_UNAUTHENTICATED=true``. Outside production the previous
    (unauthenticated) behavior is preserved for trusted-mesh / local use.
    """
    if not _is_production():
        return True
    raw = os.environ.get(_ENV_ALLOW_UNAUTH, "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def warn_if_unauthenticated_in_production() -> None:
    """Emit a one-shot CRITICAL log when A2A runs unsigned in production."""
    global _warned_unauthenticated
    if _warned_unauthenticated or get_a2a_shared_secret() is not None:
        return
    if _is_production():
        logger.critical(
            "A2A endpoints are UNAUTHENTICATED in production. Any peer that "
            "can reach the endpoint can invoke this agent. Set "
            "BASELITH_A2A_SHARED_SECRET on all peers to enable HMAC signing "
            "(or BASELITH_A2A_ALLOW_UNAUTHENTICATED=true to explicitly opt in)."
        )
    _warned_unauthenticated = True


def _compute_signature(
    body: bytes,
    timestamp: str,
    secret: str,
    nonce: str | None = None,
    peer: str | None = None,
) -> str:
    message = timestamp.encode("ascii")
    if nonce:
        message += b"." + nonce.encode("ascii")
    if peer:
        # Bound inside the MAC so the header cannot be stripped or swapped:
        # relabeling a captured request as another peer invalidates it.
        message += b"." + peer.encode("ascii")
    message += b"." + body
    mac = hmac.new(secret.encode("utf-8"), message, hashlib.sha256)
    return _SIGNATURE_PREFIX + mac.hexdigest()


def build_signature_headers(body: bytes, secret: SecretStr) -> dict[str, str]:
    """Build the signature headers for an outgoing A2A request body.

    Every outgoing request carries a fresh single-use nonce bound into the
    MAC, so the receiving peer can reject replays of captured requests. With
    ``BASELITH_A2A_PEER_ID`` configured the request additionally declares —
    and MAC-binds — this instance's peer identity, letting the receiver
    verify against that peer's own secret instead of a mesh-wide one.
    """
    timestamp = str(int(time.time()))
    nonce = uuid.uuid4().hex
    peer = get_a2a_peer_id()
    signature = _compute_signature(
        body, timestamp, secret.get_secret_value(), nonce=nonce, peer=peer
    )
    headers = {
        TIMESTAMP_HEADER: timestamp,
        NONCE_HEADER: nonce,
        SIGNATURE_HEADER: signature,
    }
    if peer:
        headers[PEER_HEADER] = peer
    return headers


def verify_signature(
    body: bytes,
    timestamp_header: str | None,
    signature_header: str | None,
    secret: SecretStr | None,
    *,
    nonce_header: str | None = None,
    peer_header: str | None = None,
    max_skew_seconds: int = DEFAULT_MAX_SKEW_SECONDS,
) -> bool:
    """Verify an incoming A2A request against the shared or per-peer secret.

    Args:
        body: Raw request body bytes, exactly as received.
        timestamp_header: Value of ``X-A2A-Timestamp``, or None if absent.
        signature_header: Value of ``X-A2A-Signature``, or None if absent.
        secret: The mesh-wide shared secret (legacy path). May be None when
            only per-peer secrets are configured.
        nonce_header: Value of ``X-A2A-Nonce``. Bound into the MAC and
            enforced single-use within the skew window. Required by default;
            absent (legacy peer) is accepted only under the deprecated
            ``BASELITH_A2A_ALLOW_LEGACY_NONCELESS=true`` opt-in.
        peer_header: Value of ``X-A2A-Peer``. When present, the request MUST
            verify with that peer's entry in ``BASELITH_A2A_PEER_SECRETS``
            and with the peer id MAC-bound — an unknown peer or a relabeled
            capture is rejected; there is no fallback to the shared secret
            (that would make the header decorative).
        max_skew_seconds: Accepted clock skew / replay window.

    Returns:
        True when the signature is present, fresh, valid, and not a replay.
    """
    if not timestamp_header or not signature_header:
        return False
    if not nonce_header and not legacy_nonceless_allowed():
        logger.warning(
            "Rejected A2A request: missing nonce (legacy nonce-less signatures "
            "are disabled by default)"
        )
        return False
    try:
        timestamp = int(timestamp_header)
    except ValueError:
        return False
    if abs(time.time() - timestamp) > max_skew_seconds:
        return False

    if peer_header:
        if not _PEER_ID_RE.match(peer_header):
            logger.warning("Rejected A2A request: malformed peer id")
            return False
        peer_secret = get_a2a_peer_secrets().get(peer_header)
        if peer_secret is None:
            logger.warning("Rejected A2A request: unknown peer %s", peer_header[:32])
            return False
        signing_secret = peer_secret.get_secret_value()
    else:
        if secret is None:
            return False
        signing_secret = secret.get_secret_value()

    expected = _compute_signature(
        body,
        timestamp_header,
        signing_secret,
        nonce=nonce_header,
        peer=peer_header,
    )
    if not hmac.compare_digest(expected, signature_header):
        return False
    if nonce_header:
        # After the MAC checks out: a forged nonce can't reach this point, so
        # a repeat here is a genuine replay of a captured request. Keep the
        # entry alive for both skew directions plus slack.
        if not _get_nonce_ledger().register_once(
            nonce_header, ttl_seconds=max_skew_seconds * 2 + 1
        ):
            logger.warning("Rejected A2A request: nonce replay detected")
            return False
    return True
