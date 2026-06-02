"""Unit tests for ``core.world_model.mandates``."""

from __future__ import annotations

import time

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.world_model.mandates import (
    CartItem,
    CartMandate,
    InMemoryReplayGuard,
    IntentMandate,
    MandateChainError,
    MandateReplayError,
    MandateSignatureError,
    new_cart_id,
    new_intent_id,
    sign_cart,
    sign_intent,
    verify_chain,
    verify_signature,
)


def _now() -> float:
    return time.time()


def _make_intent(
    *,
    max_price: float = 100.0,
    expires_in: float = 3600.0,
    intent_id: str | None = None,
) -> IntentMandate:
    return IntentMandate(
        intent_id=intent_id or new_intent_id(),
        user_id="user-1",
        item_description="laptop",
        max_price_usd=max_price,
        expires_at=_now() + expires_in,
    )


def _make_cart(intent_id: str, items: list[CartItem] | None = None) -> CartMandate:
    return CartMandate(
        cart_id=new_cart_id(),
        intent_id=intent_id,
        merchant_id="merchant-1",
        items=items or [CartItem(sku="A", quantity=1, unit_price_usd=80.0)],
    )


class TestSignAndVerify:
    def test_valid_intent_chain(self) -> None:
        user_key = Ed25519PrivateKey.generate()
        merchant_key = Ed25519PrivateKey.generate()
        intent = _make_intent(max_price=100.0)
        signed_intent = sign_intent(intent, user_key)
        cart = _make_cart(intent.intent_id)
        signed_cart = sign_cart(cart, merchant_key)
        verify_chain(
            signed_intent,
            signed_cart,
            user_public_key=user_key.public_key(),
            merchant_public_key=merchant_key.public_key(),
        )

    def test_signature_verifies_in_isolation(self) -> None:
        key = Ed25519PrivateKey.generate()
        intent = _make_intent()
        signed = sign_intent(intent, key)
        verify_signature(signed, key.public_key())

    def test_wrong_key_rejects_signature(self) -> None:
        key = Ed25519PrivateKey.generate()
        attacker = Ed25519PrivateKey.generate()
        intent = _make_intent()
        signed = sign_intent(intent, key)
        with pytest.raises(MandateSignatureError):
            verify_signature(signed, attacker.public_key())


class TestChainViolations:
    def test_cart_total_exceeds_intent(self) -> None:
        user_key = Ed25519PrivateKey.generate()
        merchant_key = Ed25519PrivateKey.generate()
        intent = _make_intent(max_price=50.0)
        signed_intent = sign_intent(intent, user_key)
        cart = _make_cart(
            intent.intent_id,
            items=[CartItem(sku="X", quantity=10, unit_price_usd=20.0)],
        )
        signed_cart = sign_cart(cart, merchant_key)
        with pytest.raises(MandateChainError, match="exceeds intent"):
            verify_chain(
                signed_intent,
                signed_cart,
                user_public_key=user_key.public_key(),
                merchant_public_key=merchant_key.public_key(),
            )

    def test_cart_intent_id_mismatch(self) -> None:
        user_key = Ed25519PrivateKey.generate()
        merchant_key = Ed25519PrivateKey.generate()
        intent = _make_intent()
        other_intent = _make_intent()
        signed_intent = sign_intent(intent, user_key)
        cart = _make_cart(other_intent.intent_id)
        signed_cart = sign_cart(cart, merchant_key)
        with pytest.raises(MandateChainError, match="does not match"):
            verify_chain(
                signed_intent,
                signed_cart,
                user_public_key=user_key.public_key(),
                merchant_public_key=merchant_key.public_key(),
            )

    def test_intent_expired(self) -> None:
        user_key = Ed25519PrivateKey.generate()
        merchant_key = Ed25519PrivateKey.generate()
        intent = _make_intent(expires_in=10.0)
        signed_intent = sign_intent(intent, user_key)
        cart = _make_cart(intent.intent_id)
        signed_cart = sign_cart(cart, merchant_key)
        future = intent.expires_at + 60.0
        with pytest.raises(MandateChainError, match="expired"):
            verify_chain(
                signed_intent,
                signed_cart,
                user_public_key=user_key.public_key(),
                merchant_public_key=merchant_key.public_key(),
                now=future,
            )

    def test_tampered_cart_rejected(self) -> None:
        user_key = Ed25519PrivateKey.generate()
        merchant_key = Ed25519PrivateKey.generate()
        intent = _make_intent(max_price=200.0)
        signed_intent = sign_intent(intent, user_key)
        cart = _make_cart(intent.intent_id)
        signed_cart = sign_cart(cart, merchant_key)
        tampered_cart = CartMandate(
            cart_id=cart.cart_id,
            intent_id=cart.intent_id,
            merchant_id=cart.merchant_id,
            items=[CartItem(sku="X", quantity=999, unit_price_usd=0.01)],
            issued_at=cart.issued_at,
        )
        tampered_signed = type(signed_cart)(
            mandate=tampered_cart,
            signature_hex=signed_cart.signature_hex,
        )
        with pytest.raises(MandateSignatureError):
            verify_chain(
                signed_intent,
                tampered_signed,
                user_public_key=user_key.public_key(),
                merchant_public_key=merchant_key.public_key(),
            )


class TestReplayProtection:
    def _signed_pair(self):
        user_key = Ed25519PrivateKey.generate()
        merchant_key = Ed25519PrivateKey.generate()
        intent = _make_intent(max_price=100.0)
        signed_intent = sign_intent(intent, user_key)
        signed_cart = sign_cart(_make_cart(intent.intent_id), merchant_key)
        return signed_intent, signed_cart, user_key, merchant_key

    def test_first_use_passes_then_replay_rejected(self) -> None:
        signed_intent, signed_cart, user_key, merchant_key = self._signed_pair()
        guard = InMemoryReplayGuard()
        verify_chain(
            signed_intent,
            signed_cart,
            user_public_key=user_key.public_key(),
            merchant_public_key=merchant_key.public_key(),
            replay_guard=guard,
        )
        with pytest.raises(MandateReplayError, match="already been consumed"):
            verify_chain(
                signed_intent,
                signed_cart,
                user_public_key=user_key.public_key(),
                merchant_public_key=merchant_key.public_key(),
                replay_guard=guard,
            )

    def test_no_guard_allows_repeat_verification(self) -> None:
        """Legacy stateless behavior unchanged when no guard supplied."""
        signed_intent, signed_cart, user_key, merchant_key = self._signed_pair()
        for _ in range(3):
            verify_chain(
                signed_intent,
                signed_cart,
                user_public_key=user_key.public_key(),
                merchant_public_key=merchant_key.public_key(),
            )

    def test_failed_chain_does_not_consume_intent(self) -> None:
        """A rejected chain must not burn the intent in the replay guard."""
        user_key = Ed25519PrivateKey.generate()
        merchant_key = Ed25519PrivateKey.generate()
        intent = _make_intent(max_price=100.0)
        signed_intent = sign_intent(intent, user_key)
        over_cart = _make_cart(
            intent.intent_id,
            items=[CartItem(sku="X", quantity=10, unit_price_usd=20.0)],
        )
        signed_over = sign_cart(over_cart, merchant_key)
        guard = InMemoryReplayGuard()
        with pytest.raises(MandateChainError, match="exceeds intent"):
            verify_chain(
                signed_intent,
                signed_over,
                user_public_key=user_key.public_key(),
                merchant_public_key=merchant_key.public_key(),
                replay_guard=guard,
            )
        # Intent not consumed: a valid cart for the same intent still verifies.
        ok_cart = sign_cart(_make_cart(intent.intent_id), merchant_key)
        verify_chain(
            signed_intent,
            ok_cart,
            user_public_key=user_key.public_key(),
            merchant_public_key=merchant_key.public_key(),
            replay_guard=guard,
        )

    def test_in_memory_guard_register_once_semantics(self) -> None:
        guard = InMemoryReplayGuard()
        assert guard.register_once("k") is True
        assert guard.register_once("k") is False


class TestInvariants:
    def test_zero_price_intent_rejected(self) -> None:
        key = Ed25519PrivateKey.generate()
        with pytest.raises(ValueError):
            sign_intent(
                IntentMandate(
                    intent_id="i",
                    user_id="u",
                    item_description="x",
                    max_price_usd=0.0,
                    expires_at=_now() + 60.0,
                ),
                key,
            )

    def test_already_expired_intent_rejected(self) -> None:
        key = Ed25519PrivateKey.generate()
        with pytest.raises(ValueError):
            sign_intent(
                IntentMandate(
                    intent_id="i",
                    user_id="u",
                    item_description="x",
                    max_price_usd=10.0,
                    expires_at=_now() - 60.0,
                ),
                key,
            )

    def test_empty_cart_rejected(self) -> None:
        merchant_key = Ed25519PrivateKey.generate()
        with pytest.raises(ValueError):
            sign_cart(
                CartMandate(
                    cart_id="c",
                    intent_id="i",
                    merchant_id="m",
                    items=[],
                ),
                merchant_key,
            )

    def test_cart_item_negative_quantity_rejected(self) -> None:
        with pytest.raises(ValueError):
            CartItem(sku="x", quantity=0, unit_price_usd=1.0).line_total()

    def test_cart_item_negative_price_rejected(self) -> None:
        with pytest.raises(ValueError):
            CartItem(sku="x", quantity=1, unit_price_usd=-0.01).line_total()

    def test_new_id_helpers_are_unique(self) -> None:
        a = new_intent_id()
        b = new_intent_id()
        assert a != b
        assert a.startswith("intent_")


class TestCanonicalization:
    def test_signature_stable_across_logically_equal_payloads(self) -> None:
        key = Ed25519PrivateKey.generate()
        ts = _now()
        i1 = IntentMandate(
            intent_id="i",
            user_id="u",
            item_description="x",
            max_price_usd=10.0,
            expires_at=ts + 60.0,
            conditions={"region": "EU", "currency": "USD"},
            issued_at=ts,
        )
        i2 = IntentMandate(
            intent_id="i",
            user_id="u",
            item_description="x",
            max_price_usd=10.0,
            expires_at=ts + 60.0,
            conditions={"currency": "USD", "region": "EU"},
            issued_at=ts,
        )
        sig1 = sign_intent(i1, key).signature_hex
        sig2 = sign_intent(i2, key).signature_hex
        assert sig1 == sig2
