"""Unit tests for the payments plugin's ``MockPSPAdapter`` reference PSP."""

from __future__ import annotations

import time

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.world_model.mandates import (
    CartItem,
    CartMandate,
    InMemoryReplayGuard,
    IntentMandate,
    new_cart_id,
    new_intent_id,
    sign_cart,
    sign_intent,
)
from core.world_model.payments import (
    InMemoryReceiptStore,
    PaymentExecutor,
    execute_payment,
)
from plugins.payments import MockPSPAdapter, PaymentsPlugin

pytestmark = [pytest.mark.unit]


def _cart(*, price: float = 80.0, quantity: int = 1) -> CartMandate:
    return CartMandate(
        cart_id=new_cart_id(),
        intent_id=new_intent_id(),
        merchant_id="merchant-1",
        items=[CartItem(sku="A", quantity=quantity, unit_price_usd=price)],
    )


class TestMockPSPAdapter:
    def test_satisfies_payment_executor_protocol(self) -> None:
        assert isinstance(MockPSPAdapter(), PaymentExecutor)

    async def test_captures_under_threshold(self) -> None:
        adapter = MockPSPAdapter(decline_over_cents=10_000)
        cart = _cart(price=80.0)
        receipt = await adapter.execute(cart, payment_method_ref="pm_1")
        assert receipt.status == "captured"
        assert receipt.amount_cents == 8000
        assert receipt.intent_id == cart.intent_id
        assert receipt.cart_id == cart.cart_id
        assert receipt.merchant_id == cart.merchant_id
        assert receipt.psp == "mockpsp"
        assert receipt.transaction_id.startswith("mockpsp_")

    async def test_declines_over_threshold(self) -> None:
        adapter = MockPSPAdapter(decline_over_cents=10_000)
        receipt = await adapter.execute(_cart(price=150.0), payment_method_ref="pm_1")
        assert receipt.status == "declined"
        assert receipt.amount_cents == 15000
        assert receipt.detail  # a decline carries an explanation

    async def test_captures_everything_without_threshold(self) -> None:
        adapter = MockPSPAdapter()
        receipt = await adapter.execute(_cart(price=99999.0), payment_method_ref="pm_1")
        assert receipt.status == "captured"

    async def test_transaction_ids_are_unique(self) -> None:
        adapter = MockPSPAdapter()
        first = await adapter.execute(_cart(), payment_method_ref="pm_1")
        second = await adapter.execute(_cart(), payment_method_ref="pm_1")
        assert first.transaction_id != second.transaction_id


class TestPaymentsPlugin:
    async def test_initialize_configures_threshold(self) -> None:
        plugin = PaymentsPlugin()
        await plugin.initialize({"decline_over_cents": 5000})
        executor = plugin.get_executor()
        assert isinstance(executor, MockPSPAdapter)
        receipt = await executor.execute(_cart(price=80.0), payment_method_ref="pm_1")
        assert receipt.status == "declined"
        await plugin.shutdown()

    def test_get_executor_works_without_lifecycle(self) -> None:
        assert isinstance(PaymentsPlugin().get_executor(), MockPSPAdapter)

    async def test_end_to_end_through_execute_payment(self) -> None:
        user_key = Ed25519PrivateKey.generate()
        merchant_key = Ed25519PrivateKey.generate()
        intent = IntentMandate(
            intent_id=new_intent_id(),
            user_id="user-1",
            item_description="laptop",
            max_price_usd=100.0,
            expires_at=time.time() + 3600.0,
        )
        cart = CartMandate(
            cart_id=new_cart_id(),
            intent_id=intent.intent_id,
            merchant_id="merchant-1",
            items=[CartItem(sku="A", quantity=1, unit_price_usd=80.0)],
        )
        store = InMemoryReceiptStore()
        receipt = await execute_payment(
            sign_intent(intent, user_key),
            sign_cart(cart, merchant_key),
            user_public_key=user_key.public_key(),
            merchant_public_key=merchant_key.public_key(),
            executor=MockPSPAdapter(),
            payment_method_ref="pm_1",
            receipt_store=store,
            replay_guard=InMemoryReplayGuard(),
        )
        assert receipt.status == "captured"
        assert await store.get(receipt.transaction_id) == receipt
