"""Tests for signed IntentMandate.conditions enforcement in verify_chain."""

from __future__ import annotations

import time

import pytest
from core.world_model.mandate_conditions import evaluate_intent_conditions
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.world_model.mandates import (
    CartItem,
    CartMandate,
    IntentMandate,
    MandateChainError,
    new_cart_id,
    new_intent_id,
    sign_cart,
    sign_intent,
    verify_chain,
)

pytestmark = [pytest.mark.unit]


def _intent(conditions: dict) -> IntentMandate:
    return IntentMandate(
        intent_id=new_intent_id(),
        user_id="user-1",
        item_description="laptop",
        max_price_usd=100.0,
        expires_at=time.time() + 3600,
        conditions=conditions,
    )


def _cart(
    intent_id: str,
    *,
    merchant_id: str = "merchant-1",
    items: list[CartItem] | None = None,
) -> CartMandate:
    return CartMandate(
        cart_id=new_cart_id(),
        intent_id=intent_id,
        merchant_id=merchant_id,
        items=items or [CartItem(sku="A", quantity=1, unit_price_usd=80.0)],
    )


class TestEvaluator:
    def test_no_conditions_no_violations(self) -> None:
        intent = _intent({})
        assert evaluate_intent_conditions(intent, _cart(intent.intent_id)) == []

    def test_allowed_merchants_pass_and_fail(self) -> None:
        intent = _intent({"allowed_merchants": ["merchant-1", "merchant-2"]})
        assert evaluate_intent_conditions(intent, _cart(intent.intent_id)) == []

        rogue = _cart(intent.intent_id, merchant_id="evil-mart")
        violations = evaluate_intent_conditions(intent, rogue)
        assert len(violations) == 1
        assert "allowed_merchants" in violations[0]

    def test_max_items_counts_total_quantity(self) -> None:
        intent = _intent({"max_items": 3})
        ok = _cart(
            intent.intent_id,
            items=[CartItem(sku="A", quantity=3, unit_price_usd=1.0)],
        )
        assert evaluate_intent_conditions(intent, ok) == []

        too_many = _cart(
            intent.intent_id,
            items=[
                CartItem(sku="A", quantity=2, unit_price_usd=1.0),
                CartItem(sku="B", quantity=2, unit_price_usd=1.0),
            ],
        )
        violations = evaluate_intent_conditions(intent, too_many)
        assert violations and "max_items" in violations[0]

    def test_allowed_skus(self) -> None:
        intent = _intent({"allowed_skus": ["A", "B"]})
        bad = _cart(
            intent.intent_id,
            items=[CartItem(sku="C", quantity=1, unit_price_usd=1.0)],
        )
        violations = evaluate_intent_conditions(intent, bad)
        assert violations and "allowed_skus" in violations[0]

    def test_unknown_condition_fails_closed(self) -> None:
        """A signed condition the verifier cannot evaluate must not be ignored."""
        intent = _intent({"only_fair_trade": True})
        violations = evaluate_intent_conditions(intent, _cart(intent.intent_id))
        assert violations and "only_fair_trade" in violations[0]


class TestVerifyChainEnforcement:
    def _signed_pair(self, conditions: dict, *, merchant_id: str = "merchant-1"):
        user_key = Ed25519PrivateKey.generate()
        merchant_key = Ed25519PrivateKey.generate()
        intent = _intent(conditions)
        cart = _cart(intent.intent_id, merchant_id=merchant_id)
        return (
            sign_intent(intent, user_key),
            sign_cart(cart, merchant_key),
            user_key.public_key(),
            merchant_key.public_key(),
        )

    def test_violating_cart_rejected(self) -> None:
        si, sc, upub, mpub = self._signed_pair(
            {"allowed_merchants": ["merchant-1"]}, merchant_id="evil-mart"
        )
        with pytest.raises(MandateChainError, match="condition"):
            verify_chain(
                si,
                sc,
                user_public_key=upub,
                merchant_public_key=mpub,
                replay_guard=None,
            )

    def test_conforming_cart_passes(self) -> None:
        si, sc, upub, mpub = self._signed_pair({"allowed_merchants": ["merchant-1"]})
        verify_chain(
            si,
            sc,
            user_public_key=upub,
            merchant_public_key=mpub,
            replay_guard=None,
        )

    def test_kill_switch_disables_enforcement(self, monkeypatch) -> None:
        monkeypatch.setenv("BASELITH_AP2_ENFORCE_CONDITIONS", "0")
        si, sc, upub, mpub = self._signed_pair(
            {"allowed_merchants": ["merchant-1"]}, merchant_id="evil-mart"
        )
        verify_chain(
            si,
            sc,
            user_public_key=upub,
            merchant_public_key=mpub,
            replay_guard=None,
        )

    def test_rejected_conditions_do_not_consume_intent(self) -> None:
        """A condition failure must leave the intent replayable (fixable cart)."""
        from core.world_model.mandates import InMemoryReplayGuard

        user_key = Ed25519PrivateKey.generate()
        merchant_key = Ed25519PrivateKey.generate()
        intent = _intent({"allowed_merchants": ["merchant-1"]})
        si = sign_intent(intent, user_key)
        guard = InMemoryReplayGuard()

        bad = sign_cart(_cart(intent.intent_id, merchant_id="evil-mart"), merchant_key)
        with pytest.raises(MandateChainError):
            verify_chain(
                si,
                bad,
                user_public_key=user_key.public_key(),
                merchant_public_key=merchant_key.public_key(),
                replay_guard=guard,
            )

        good = sign_cart(_cart(intent.intent_id), merchant_key)
        verify_chain(
            si,
            good,
            user_public_key=user_key.public_key(),
            merchant_public_key=merchant_key.public_key(),
            replay_guard=guard,
        )
