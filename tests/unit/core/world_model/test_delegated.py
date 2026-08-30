"""Unit tests for ``core.world_model.delegated`` — human-not-present mode."""

from __future__ import annotations

import time

import pytest
from core.world_model.delegated import (
    DelegatedPurchaseAgent,
    DelegationRegistryFullError,
)
from core.world_model.payments import InMemoryReceiptStore, PaymentReceipt
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from core.world_model.mandates import (
    CartItem,
    CartMandate,
    InMemoryReplayGuard,
    IntentMandate,
    MandateChainError,
    MandateSignatureError,
    SignedMandate,
    new_cart_id,
    new_intent_id,
    sign_cart,
    sign_intent,
)

pytestmark = [pytest.mark.unit]


class _RecordingExecutor:
    def __init__(self, status: str = "captured") -> None:
        self.status = status
        self.calls: list[tuple[CartMandate, str]] = []

    async def execute(
        self, cart: CartMandate, *, payment_method_ref: str
    ) -> PaymentReceipt:
        self.calls.append((cart, payment_method_ref))
        return PaymentReceipt(
            transaction_id=f"testpsp_txn_{len(self.calls)}",
            intent_id=cart.intent_id,
            cart_id=cart.cart_id,
            merchant_id=cart.merchant_id,
            amount_cents=cart.total_cents(),
            status=self.status,  # type: ignore[arg-type]
            executed_at=time.time(),
            psp="testpsp",
        )


class _Env:
    """One user, one merchant, injected guard/store — fully deterministic."""

    def __init__(self, executor: _RecordingExecutor, max_intents: int = 1000) -> None:
        self.user_key = Ed25519PrivateKey.generate()
        self.merchant_key = Ed25519PrivateKey.generate()
        self.executor = executor
        self.store = InMemoryReceiptStore()
        self.agent = DelegatedPurchaseAgent(
            executor,
            user_public_key_resolver=self.resolve,
            receipt_store=self.store,
            replay_guard=InMemoryReplayGuard(),
            max_intents=max_intents,
        )

    def resolve(self, user_id: str) -> Ed25519PublicKey:
        if user_id != "user-1":
            raise KeyError(user_id)
        return self.user_key.public_key()

    def signed_intent(
        self,
        *,
        max_price: float = 100.0,
        expires_at: float | None = None,
        issued_at: float | None = None,
        conditions: dict | None = None,
    ) -> SignedMandate:
        intent = IntentMandate(
            intent_id=new_intent_id(),
            user_id="user-1",
            item_description="laptop",
            max_price_usd=max_price,
            expires_at=expires_at if expires_at is not None else time.time() + 3600.0,
            conditions=conditions or {},
            issued_at=issued_at if issued_at is not None else time.time(),
        )
        return sign_intent(intent, self.user_key)

    def signed_cart(
        self,
        intent_id: str,
        *,
        price: float = 80.0,
        merchant_id: str = "merchant-1",
    ) -> SignedMandate:
        cart = CartMandate(
            cart_id=new_cart_id(),
            intent_id=intent_id,
            merchant_id=merchant_id,
            items=[CartItem(sku="A", quantity=1, unit_price_usd=price)],
        )
        return sign_cart(cart, self.merchant_key)

    async def offer(
        self, signed_cart: SignedMandate, **kwargs
    ) -> PaymentReceipt | None:
        return await self.agent.evaluate_offer(
            signed_cart,
            merchant_public_key=self.merchant_key.public_key(),
            payment_method_ref="pm_delegated_1",
            **kwargs,
        )


class TestRegister:
    def test_rejects_bad_signature(self) -> None:
        env = _Env(_RecordingExecutor())
        attacker = Ed25519PrivateKey.generate()
        intent = IntentMandate(
            intent_id=new_intent_id(),
            user_id="user-1",
            item_description="laptop",
            max_price_usd=100.0,
            expires_at=time.time() + 3600.0,
        )
        forged = sign_intent(intent, attacker)
        with pytest.raises(MandateSignatureError):
            env.agent.register(forged)

    def test_rejects_expired_intent(self) -> None:
        env = _Env(_RecordingExecutor())
        stale = env.signed_intent(
            issued_at=time.time() - 100.0, expires_at=time.time() - 10.0
        )
        with pytest.raises(MandateChainError):
            env.agent.register(stale)

    def test_rejects_non_intent_mandate(self) -> None:
        env = _Env(_RecordingExecutor())
        signed_cart = env.signed_cart("intent-x")
        with pytest.raises(MandateChainError):
            env.agent.register(signed_cart)

    def test_registry_is_bounded(self) -> None:
        env = _Env(_RecordingExecutor(), max_intents=2)
        env.agent.register(env.signed_intent())
        env.agent.register(env.signed_intent())
        with pytest.raises(DelegationRegistryFullError):
            env.agent.register(env.signed_intent())


class TestEvaluateOffer:
    async def test_unknown_intent_returns_none_with_zero_side_effects(self) -> None:
        env = _Env(_RecordingExecutor())
        result = await env.offer(env.signed_cart("intent-unknown"))
        assert result is None
        assert env.executor.calls == []
        assert await env.store.list_for_intent("intent-unknown") == []

    async def test_expired_intent_returns_none(self) -> None:
        env = _Env(_RecordingExecutor())
        signed = env.signed_intent()
        env.agent.register(signed)
        intent = signed.mandate
        assert isinstance(intent, IntentMandate)
        result = await env.offer(
            env.signed_cart(intent.intent_id), now=intent.expires_at + 1.0
        )
        assert result is None
        assert env.executor.calls == []

    async def test_condition_violation_returns_none_without_consuming(self) -> None:
        env = _Env(_RecordingExecutor())
        signed = env.signed_intent(conditions={"allowed_merchants": ["merchant-1"]})
        env.agent.register(signed)
        intent = signed.mandate
        assert isinstance(intent, IntentMandate)
        rogue = await env.offer(
            env.signed_cart(intent.intent_id, merchant_id="evil-merch")
        )
        assert rogue is None
        assert env.executor.calls == []
        # The intent was not consumed: a conforming cart still purchases.
        receipt = await env.offer(env.signed_cart(intent.intent_id))
        assert receipt is not None
        assert receipt.status == "captured"

    async def test_over_cap_returns_none_without_consuming(self) -> None:
        env = _Env(_RecordingExecutor())
        signed = env.signed_intent(max_price=100.0)
        env.agent.register(signed)
        intent = signed.mandate
        assert isinstance(intent, IntentMandate)
        greedy = await env.offer(env.signed_cart(intent.intent_id, price=150.0))
        assert greedy is None
        assert env.executor.calls == []
        receipt = await env.offer(env.signed_cart(intent.intent_id, price=80.0))
        assert receipt is not None
        assert receipt.status == "captured"

    async def test_conforming_offer_executes_and_deregisters(self) -> None:
        env = _Env(_RecordingExecutor())
        signed = env.signed_intent()
        env.agent.register(signed)
        intent = signed.mandate
        assert isinstance(intent, IntentMandate)
        receipt = await env.offer(env.signed_cart(intent.intent_id))
        assert receipt is not None
        assert receipt.status == "captured"
        assert len(env.executor.calls) == 1
        assert await env.store.get(receipt.transaction_id) == receipt
        # One intent = max one purchase: the second offer finds nothing.
        second = await env.offer(env.signed_cart(intent.intent_id))
        assert second is None
        assert len(env.executor.calls) == 1

    async def test_consumed_but_registered_intent_is_replay_safe(self) -> None:
        # A declined charge still consumes the intent via the replay guard,
        # while leaving it registered; the next offer must resolve to None
        # rather than surface MandateReplayError or charge again.
        env = _Env(_RecordingExecutor(status="declined"))
        signed = env.signed_intent()
        env.agent.register(signed)
        intent = signed.mandate
        assert isinstance(intent, IntentMandate)
        declined = await env.offer(env.signed_cart(intent.intent_id))
        assert declined is not None
        assert declined.status == "declined"
        second = await env.offer(env.signed_cart(intent.intent_id))
        assert second is None
        assert len(env.executor.calls) == 1

    async def test_non_cart_offer_is_rejected(self) -> None:
        env = _Env(_RecordingExecutor())
        with pytest.raises(MandateChainError):
            await env.offer(env.signed_intent())
