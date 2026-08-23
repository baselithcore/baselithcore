"""Chain-rule hardening for the AP2 mandate chain.

Split out of ``test_mandates.py`` (500-line cap). These cover the rules that
constrain a cart *relative to* its intent — freshness, merchant binding — plus
the bound on the in-memory replay ledger. Signing and signature verification
stay in ``test_mandates.py``.
"""

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
    new_cart_id,
    new_intent_id,
    sign_cart,
    sign_intent,
    verify_chain,
)


def _now() -> float:
    return time.time()


def _make_intent(
    *,
    max_price: float = 100.0,
    expires_in: float = 3600.0,
) -> IntentMandate:
    return IntentMandate(
        intent_id=new_intent_id(),
        user_id="user-1",
        item_description="laptop",
        max_price_usd=max_price,
        expires_at=_now() + expires_in,
    )


class TestCartFreshness:
    """`CartMandate` carries no expiry of its own, so without these checks a
    cart stays verifiable for the whole intent window — long after the quoted
    prices stopped being current — and a forged/replayed envelope can carry an
    arbitrary `issued_at`."""

    def _chain(
        self,
        *,
        cart_age: float = 0.0,
        cart_issued_at: float | None = None,
        intent_issued_at: float | None = None,
    ):
        """Build a chain whose cart was issued ``cart_age`` seconds ago.

        By default the intent is dated slightly *before* the cart, mirroring
        reality: the user signs the intent, the merchant then signs a cart
        against it. ``intent_issued_at`` overrides that to construct the
        illegal ordering (cart older than the intent it references).
        """
        user_key = Ed25519PrivateKey.generate()
        merchant_key = Ed25519PrivateKey.generate()
        cart_at = cart_issued_at if cart_issued_at is not None else _now() - cart_age
        intent = IntentMandate(
            intent_id=new_intent_id(),
            user_id="user-1",
            item_description="laptop",
            max_price_usd=100.0,
            expires_at=_now() + 3600.0,
            issued_at=(
                intent_issued_at
                if intent_issued_at is not None
                else min(cart_at, _now()) - 10.0
            ),
        )
        signed_intent = sign_intent(intent, user_key)
        cart = CartMandate(
            cart_id=new_cart_id(),
            intent_id=intent.intent_id,
            merchant_id="merchant-1",
            items=[CartItem(sku="A", quantity=1, unit_price_usd=80.0)],
            issued_at=cart_at,
        )
        signed_cart = sign_cart(cart, merchant_key)
        return signed_intent, signed_cart, user_key, merchant_key

    def _verify(self, chain, **kwargs) -> None:
        signed_intent, signed_cart, user_key, merchant_key = chain
        verify_chain(
            signed_intent,
            signed_cart,
            user_public_key=user_key.public_key(),
            merchant_public_key=merchant_key.public_key(),
            replay_guard=None,
            **kwargs,
        )

    def test_fresh_cart_passes(self) -> None:
        self._verify(self._chain())

    def test_cart_dated_in_the_future_is_rejected(self) -> None:
        chain = self._chain(cart_issued_at=_now() + 3600.0)
        with pytest.raises(MandateChainError, match="future"):
            self._verify(chain)

    def test_small_future_skew_is_tolerated(self) -> None:
        """Ordinary NTP drift between the two signers must not reject a
        legitimate chain."""
        self._verify(self._chain(cart_issued_at=_now() + 5.0))

    def test_cart_predating_the_intent_is_rejected(self) -> None:
        """A merchant signs the cart *against* the intent, so it cannot
        legitimately have been issued before it."""
        # Cart an hour old, intent minted just now: the illegal ordering.
        chain = self._chain(cart_issued_at=_now() - 3600.0, intent_issued_at=_now())
        with pytest.raises(MandateChainError, match="predates"):
            self._verify(chain)

    def test_max_cart_age_rejects_a_stale_cart(self) -> None:
        chain = self._chain(cart_issued_at=_now() - 600.0)
        with pytest.raises(MandateChainError, match="old"):
            self._verify(chain, max_cart_age_seconds=300.0)

    def test_max_cart_age_accepts_a_cart_within_the_window(self) -> None:
        self._verify(
            self._chain(cart_issued_at=_now() - 60.0), max_cart_age_seconds=300.0
        )

    def test_no_max_age_keeps_the_previous_behaviour(self) -> None:
        """Backwards compatible: without the limit an older cart still passes."""
        self._verify(self._chain(cart_issued_at=_now() - 600.0))


class TestMerchantBinding:
    """`merchant_public_key` alone does not bind the cart to a merchant: a
    caller holding one trusted key would otherwise accept a cart *claiming* any
    merchant_id, as long as that key signed it."""

    def _chain(self, merchant_id: str):
        user_key = Ed25519PrivateKey.generate()
        merchant_key = Ed25519PrivateKey.generate()
        intent = _make_intent()
        signed_intent = sign_intent(intent, user_key)
        cart = CartMandate(
            cart_id=new_cart_id(),
            intent_id=intent.intent_id,
            merchant_id=merchant_id,
            items=[CartItem(sku="A", quantity=1, unit_price_usd=10.0)],
        )
        return signed_intent, sign_cart(cart, merchant_key), user_key, merchant_key

    def _verify(self, chain, **kwargs) -> None:
        signed_intent, signed_cart, user_key, merchant_key = chain
        verify_chain(
            signed_intent,
            signed_cart,
            user_public_key=user_key.public_key(),
            merchant_public_key=merchant_key.public_key(),
            replay_guard=None,
            **kwargs,
        )

    def test_matching_merchant_id_passes(self) -> None:
        self._verify(self._chain("merchant-1"), expected_merchant_id="merchant-1")

    def test_mismatched_merchant_id_is_rejected(self) -> None:
        with pytest.raises(MandateChainError, match="merchant"):
            self._verify(
                self._chain("attacker-shop"), expected_merchant_id="merchant-1"
            )

    def test_unset_expectation_keeps_the_previous_behaviour(self) -> None:
        self._verify(self._chain("any-merchant"))


class TestReplayGuardBound:
    """The in-memory ledger must not grow without limit in a long-lived
    process."""

    def test_oldest_entries_are_evicted_past_the_bound(self) -> None:
        guard = InMemoryReplayGuard(max_entries=10)
        for i in range(25):
            assert guard.register_once(f"intent-{i}") is True
        assert len(guard._seen) <= 10
        # The most recent intent is still remembered (replay still refused).
        assert guard.register_once("intent-24") is False
