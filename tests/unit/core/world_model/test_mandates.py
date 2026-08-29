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

    def test_default_guard_rejects_replay_out_of_the_box(self) -> None:
        """Replay protection is ON by default: omitting the guard must not
        leave a signed purchase authorization replayable."""
        signed_intent, signed_cart, user_key, merchant_key = self._signed_pair()
        verify_chain(
            signed_intent,
            signed_cart,
            user_public_key=user_key.public_key(),
            merchant_public_key=merchant_key.public_key(),
        )
        with pytest.raises(MandateReplayError, match="already been consumed"):
            verify_chain(
                signed_intent,
                signed_cart,
                user_public_key=user_key.public_key(),
                merchant_public_key=merchant_key.public_key(),
            )

    def test_explicit_none_opts_into_stateless_verification(self) -> None:
        """``replay_guard=None`` is the explicit, auditable stateless opt-out."""
        signed_intent, signed_cart, user_key, merchant_key = self._signed_pair()
        for _ in range(3):
            verify_chain(
                signed_intent,
                signed_cart,
                user_public_key=user_key.public_key(),
                merchant_public_key=merchant_key.public_key(),
                replay_guard=None,
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


class TestMalformedSignature:
    """A signature field arriving from a peer is untrusted input: a non-hex
    value must be a clean MandateSignatureError, not an unhandled ValueError
    escaping the verification boundary as a 500."""

    def _signed(self, signature_hex: str):
        from core.world_model.mandates import SignedMandate

        ts = _now()
        intent = IntentMandate(
            intent_id="i",
            user_id="u",
            item_description="x",
            max_price_usd=10.0,
            expires_at=ts + 60.0,
            issued_at=ts,
        )
        return SignedMandate(mandate=intent, signature_hex=signature_hex)

    @pytest.mark.parametrize("bad", ["zz", "not-hex", "abc"])
    def test_non_hex_signature_rejected_cleanly(self, bad: str) -> None:
        with pytest.raises(MandateSignatureError):
            _ = self._signed(bad).signature

    def test_non_hex_signature_rejected_during_verification(self) -> None:
        key = Ed25519PrivateKey.generate()
        with pytest.raises(MandateSignatureError):
            verify_signature(self._signed("nothex"), key.public_key())


class TestIntegerCentsComparison:
    """Money comparisons run in integer cents: binary-float accumulation must
    neither reject a legitimate cart nor admit one above the cap."""

    def _verify(self, *, max_price: float, items: list[CartItem]) -> None:
        user_key = Ed25519PrivateKey.generate()
        merchant_key = Ed25519PrivateKey.generate()
        intent = _make_intent(max_price=max_price)
        cart = _make_cart(intent.intent_id, items=items)
        verify_chain(
            sign_intent(intent, user_key),
            sign_cart(cart, merchant_key),
            user_public_key=user_key.public_key(),
            merchant_public_key=merchant_key.public_key(),
            replay_guard=None,
        )

    def test_float_accumulation_does_not_reject_exact_cap(self) -> None:
        """3 x $0.10 against a $0.30 cap: float sum is 0.30000000000000004,
        which used to read as 'over budget' and reject a legitimate cart."""
        self._verify(
            max_price=0.30,
            items=[
                CartItem(sku=f"S{i}", quantity=1, unit_price_usd=0.10) for i in range(3)
            ],
        )

    def test_one_cent_over_cap_is_rejected(self) -> None:
        with pytest.raises(MandateChainError, match="exceeds intent max"):
            self._verify(
                max_price=0.30,
                items=[CartItem(sku="A", quantity=1, unit_price_usd=0.31)],
            )

    def test_cents_helpers_are_exact(self) -> None:
        item = CartItem(sku="A", quantity=3, unit_price_usd=0.10)
        assert item.line_total_cents() == 30
        cart = _make_cart(
            "i", items=[item, CartItem(sku="B", quantity=1, unit_price_usd=19.99)]
        )
        assert cart.total_cents() == 30 + 1999
