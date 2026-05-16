"""
AP2 mandate chain for agent-initiated commerce.

Implements the protocol from book ch8: every autonomous purchase requires
a signed ``IntentMandate`` from the user, followed by a ``CartMandate``
the merchant signs against the intent. Verification walks the chain so a
malicious cart cannot exceed the user-authorized envelope.

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
from typing import Any

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
        return bytes.fromhex(self.signature_hex)


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


def verify_chain(
    signed_intent: SignedMandate,
    signed_cart: SignedMandate,
    *,
    user_public_key: Ed25519PublicKey,
    merchant_public_key: Ed25519PublicKey,
    now: float | None = None,
) -> None:
    """Verify both signatures and enforce the cart-vs-intent rules."""
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
