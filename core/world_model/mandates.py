"""
AP2 mandate chain for agent-initiated commerce.

Every autonomous purchase requires a signed ``IntentMandate`` from the
user, followed by a ``CartMandate`` the merchant signs against the intent.
Verification walks the chain so a malicious cart cannot exceed the
user-authorized envelope.

Signatures use Ed25519 (small, fast, modern). Mandates are content-hashed
canonically (sorted JSON keys, no whitespace) before signing so semantically
identical objects always produce identical signatures.

This module owns the *protocol*. Key management, storage, and execution
of the purchase live elsewhere.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol, runtime_checkable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


class MandateError(RuntimeError):
    """Base error for any mandate-chain violation."""


class MandateSignatureError(MandateError):
    """Raised when a mandate signature fails verification."""


class MandateChainError(MandateError):
    """Raised when the cart-vs-intent chain rules are violated."""


class MandateReplayError(MandateChainError):
    """Raised when a mandate chain is re-submitted (already-consumed intent)."""


@runtime_checkable
class ReplayGuard(Protocol):
    """Single-use ledger for consumed intents.

    A signed intent+cart chain is otherwise valid forever inside the intent's
    expiry window — nothing stops an attacker (or a buggy retry) from replaying
    the same authorized purchase. A ``ReplayGuard`` records consumed intents so
    ``verify_chain`` can reject the second use.

    Implementations must make ``register_once`` atomic: in a multi-process or
    multi-worker deployment, back it with Redis ``SET key value NX`` (or an
    equivalent compare-and-set) rather than the in-memory default.
    """

    def register_once(self, key: str) -> bool:
        """Register ``key`` as consumed.

        Returns:
            True if ``key`` was newly recorded (first use); False if it was
            already present (a replay).
        """
        ...


class InMemoryReplayGuard:
    """Process-local :class:`ReplayGuard` backed by a set.

    Suitable for single-process deployments and tests. It does **not** survive
    restarts or coordinate across workers — use a Redis-backed guard in
    production (see the class docstring on :class:`ReplayGuard`).
    """

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def register_once(self, key: str) -> bool:
        if key in self._seen:
            return False
        self._seen.add(key)
        return True


def _now() -> float:
    return time.time()


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    """Canonicalize a payload for signing: sorted keys, no whitespace, UTF-8."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True)
class CartItem:
    """A single line on a cart."""

    sku: str
    quantity: int
    unit_price_usd: float

    def line_total(self) -> float:
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.unit_price_usd < 0:
            raise ValueError("unit_price_usd must be non-negative")
        return self.quantity * self.unit_price_usd


@dataclass(frozen=True)
class IntentMandate:
    """User-signed envelope authorizing an agent to spend up to ``max_price_usd``."""

    intent_id: str
    user_id: str
    item_description: str
    max_price_usd: float
    expires_at: float
    conditions: dict[str, Any] = field(default_factory=dict)
    issued_at: float = field(default_factory=_now)

    def to_canonical(self) -> bytes:
        return _canonical_bytes(asdict(self))


@dataclass(frozen=True)
class CartMandate:
    """Merchant-signed cart pinned to a specific ``IntentMandate``."""

    cart_id: str
    intent_id: str
    merchant_id: str
    items: list[CartItem]
    issued_at: float = field(default_factory=_now)

    def total_usd(self) -> float:
        return sum(item.line_total() for item in self.items)

    def to_canonical(self) -> bytes:
        payload = {
            "cart_id": self.cart_id,
            "intent_id": self.intent_id,
            "merchant_id": self.merchant_id,
            "items": [asdict(item) for item in self.items],
            "issued_at": self.issued_at,
        }
        return _canonical_bytes(payload)


@dataclass(frozen=True)
class SignedMandate:
    """A mandate plus a detached Ed25519 signature."""

    mandate: IntentMandate | CartMandate
    signature_hex: str

    @property
    def signature(self) -> bytes:
        # signature_hex arrives from a peer, so it is untrusted input: a
        # non-hex value must be a clean rejection, not an unhandled ValueError
        # escaping the verification boundary as a 500.
        try:
            return bytes.fromhex(self.signature_hex)
        except ValueError as exc:
            raise MandateSignatureError("signature is not valid hex") from exc


def new_intent_id(prefix: str = "intent") -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def new_cart_id(prefix: str = "cart") -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def sign_intent(
    mandate: IntentMandate, private_key: Ed25519PrivateKey
) -> SignedMandate:
    """Sign an ``IntentMandate`` with the user's private key."""
    if mandate.max_price_usd <= 0:
        raise ValueError("max_price_usd must be positive")
    if mandate.expires_at <= mandate.issued_at:
        raise ValueError("expires_at must be after issued_at")
    sig = private_key.sign(mandate.to_canonical())
    return SignedMandate(mandate=mandate, signature_hex=sig.hex())


def sign_cart(mandate: CartMandate, private_key: Ed25519PrivateKey) -> SignedMandate:
    """Sign a ``CartMandate`` with the merchant's private key."""
    if not mandate.items:
        raise ValueError("cart must contain at least one item")
    sig = private_key.sign(mandate.to_canonical())
    return SignedMandate(mandate=mandate, signature_hex=sig.hex())


def verify_signature(signed: SignedMandate, public_key: Ed25519PublicKey) -> None:
    """Verify the detached signature. Raises ``MandateSignatureError`` on failure."""
    try:
        public_key.verify(signed.signature, signed.mandate.to_canonical())
    except InvalidSignature as exc:
        raise MandateSignatureError(
            "signature for mandate failed verification"
        ) from exc


class _UseDefaultGuard:
    """Sentinel: caller did not choose a guard — use the module default."""


_USE_DEFAULT_GUARD = _UseDefaultGuard()

# Replay protection is ON by default: a signed intent+cart chain authorizes a
# purchase, and without a single-use ledger the same chain verifies unlimited
# times within the intent expiry window. The default is resolved lazily on
# first use (see core.world_model.replay_guard.build_default_replay_guard): a
# Redis-backed shared ledger when CACHE_REDIS_URL is configured — the only
# correct choice once more than one worker runs, since a process-local ledger
# degrades to one execution PER WORKER — otherwise the in-memory guard, with a
# production warning. A caller that genuinely wants stateless verification
# still opts out explicitly with ``replay_guard=None``.
_DEFAULT_REPLAY_GUARD: ReplayGuard | None = None


def _default_replay_guard() -> ReplayGuard:
    """Lazily build (and memoize) the process-wide default replay guard."""
    global _DEFAULT_REPLAY_GUARD
    if _DEFAULT_REPLAY_GUARD is None:
        from core.world_model.replay_guard import build_default_replay_guard

        _DEFAULT_REPLAY_GUARD = build_default_replay_guard()
    return _DEFAULT_REPLAY_GUARD


def verify_chain(
    signed_intent: SignedMandate,
    signed_cart: SignedMandate,
    *,
    user_public_key: Ed25519PublicKey,
    merchant_public_key: Ed25519PublicKey,
    now: float | None = None,
    replay_guard: ReplayGuard | None | _UseDefaultGuard = _USE_DEFAULT_GUARD,
) -> None:
    """Verify both signatures and enforce the cart-vs-intent rules.

    Args:
        signed_intent: User-signed :class:`IntentMandate`.
        signed_cart: Merchant-signed :class:`CartMandate` pinned to the intent.
        user_public_key: Public key the intent was signed with.
        merchant_public_key: Public key the cart was signed with.
        now: Override for the current time (testing).
        replay_guard: Single-use ledger consuming the intent exactly once: a
            second verification of the same intent raises
            :class:`MandateReplayError`. Defaults to the strongest guard the
            deployment supports — a Redis-backed shared ledger when
            ``CACHE_REDIS_URL`` is configured, otherwise a process-local
            in-memory one (which only protects a single worker; a production
            deployment without Redis is warned about at first use). Pass an
            explicit implementation to override, or ``None`` to opt into
            stateless verification with no replay protection. Consumption
            happens only after every other check passes, so a rejected chain
            never burns a legitimate intent.
    """
    if isinstance(replay_guard, _UseDefaultGuard):
        replay_guard = _default_replay_guard()
    intent = signed_intent.mandate
    cart = signed_cart.mandate
    if not isinstance(intent, IntentMandate):
        raise MandateChainError("signed_intent must wrap an IntentMandate")
    if not isinstance(cart, CartMandate):
        raise MandateChainError("signed_cart must wrap a CartMandate")
    verify_signature(signed_intent, user_public_key)
    verify_signature(signed_cart, merchant_public_key)
    if cart.intent_id != intent.intent_id:
        raise MandateChainError(
            f"cart.intent_id={cart.intent_id} does not match "
            f"intent.intent_id={intent.intent_id}"
        )
    current = now if now is not None else _now()
    if current >= intent.expires_at:
        raise MandateChainError(f"intent expired at {intent.expires_at}, now {current}")
    total = cart.total_usd()
    if total > intent.max_price_usd:
        raise MandateChainError(
            f"cart total ${total:.2f} exceeds intent max ${intent.max_price_usd:.2f}"
        )
    if replay_guard is not None and not replay_guard.register_once(intent.intent_id):
        raise MandateReplayError(
            f"intent {intent.intent_id} has already been consumed (replay)"
        )
