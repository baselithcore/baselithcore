"""Unit tests for ``core.world_model.payments`` — the AP2 execution seam."""

from __future__ import annotations

import time
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from core.observability import audit as audit_module
from core.observability.audit import AuditEventType
from core.world_model.mandates import (
    CartItem,
    CartMandate,
    InMemoryReplayGuard,
    IntentMandate,
    MandateChainError,
    MandateReplayError,
    MandateSignatureError,
    SignedMandate,
    new_cart_id,
    new_intent_id,
    sign_cart,
    sign_intent,
)
from core.world_model.payments import (
    InMemoryReceiptStore,
    PaymentExecutionError,
    PaymentExecutor,
    PaymentReceipt,
    execute_payment,
)

pytestmark = [pytest.mark.unit]


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def write(self, event: Any) -> None:
        self.events.append(event)


@pytest.fixture
def audit_sink(monkeypatch: pytest.MonkeyPatch) -> _RecordingSink:
    sink = _RecordingSink()
    logger = audit_module.AuditLogger(sinks=[sink])
    monkeypatch.setattr(audit_module, "get_audit_logger", lambda: logger)
    return sink


class _RecordingExecutor:
    """Executor stub returning a canned receipt and recording every call."""

    def __init__(self, status: str = "captured", psp: str = "testpsp") -> None:
        self.status = status
        self.psp = psp
        self.calls: list[tuple[CartMandate, str]] = []

    async def execute(
        self, cart: CartMandate, *, payment_method_ref: str
    ) -> PaymentReceipt:
        self.calls.append((cart, payment_method_ref))
        return PaymentReceipt(
            transaction_id=f"{self.psp}_txn_{len(self.calls)}",
            intent_id=cart.intent_id,
            cart_id=cart.cart_id,
            merchant_id=cart.merchant_id,
            amount_cents=cart.total_cents(),
            status=self.status,  # type: ignore[arg-type]
            executed_at=time.time(),
            psp=self.psp,
        )


class _RaisingExecutor:
    """Executor stub simulating a PSP infrastructure failure."""

    psp_name = "brokenpsp"

    def __init__(self) -> None:
        self.calls = 0

    async def execute(
        self, cart: CartMandate, *, payment_method_ref: str
    ) -> PaymentReceipt:
        self.calls += 1
        raise ConnectionError("psp gateway down")


def _chain(
    *, max_price: float = 100.0, price: float = 80.0
) -> tuple[SignedMandate, SignedMandate, Ed25519PublicKey, Ed25519PublicKey]:
    user_key = Ed25519PrivateKey.generate()
    merchant_key = Ed25519PrivateKey.generate()
    intent = IntentMandate(
        intent_id=new_intent_id(),
        user_id="user-1",
        item_description="laptop",
        max_price_usd=max_price,
        expires_at=time.time() + 3600.0,
    )
    cart = CartMandate(
        cart_id=new_cart_id(),
        intent_id=intent.intent_id,
        merchant_id="merchant-1",
        items=[CartItem(sku="A", quantity=1, unit_price_usd=price)],
    )
    return (
        sign_intent(intent, user_key),
        sign_cart(cart, merchant_key),
        user_key.public_key(),
        merchant_key.public_key(),
    )


class TestPaymentReceipt:
    def test_to_dict_round_trips_all_fields(self) -> None:
        receipt = PaymentReceipt(
            transaction_id="txn-1",
            intent_id="intent-1",
            cart_id="cart-1",
            merchant_id="merchant-1",
            amount_cents=8000,
            status="captured",
            executed_at=123.0,
            psp="testpsp",
        )
        data = receipt.to_dict()
        assert data["transaction_id"] == "txn-1"
        assert data["amount_cents"] == 8000
        assert data["currency"] == "USD"
        assert data["status"] == "captured"
        assert data["detail"] == ""

    def test_executor_protocol_is_runtime_checkable(self) -> None:
        assert isinstance(_RecordingExecutor(), PaymentExecutor)
        assert not isinstance(object(), PaymentExecutor)


class TestInMemoryReceiptStore:
    async def test_get_missing_returns_none(self) -> None:
        store = InMemoryReceiptStore()
        assert await store.get("nope") is None
        assert await store.list_for_intent("nope") == []

    async def test_record_is_idempotent_per_transaction(self) -> None:
        store = InMemoryReceiptStore()
        receipt = PaymentReceipt(
            transaction_id="txn-1",
            intent_id="intent-1",
            cart_id="cart-1",
            merchant_id="merchant-1",
            amount_cents=100,
            status="captured",
            executed_at=1.0,
            psp="testpsp",
        )
        await store.record(receipt)
        await store.record(receipt)
        assert await store.list_for_intent("intent-1") == [receipt]


class TestExecutePayment:
    async def test_happy_path_records_receipt_and_audits(
        self, audit_sink: _RecordingSink
    ) -> None:
        signed_intent, signed_cart, user_pk, merchant_pk = _chain()
        executor = _RecordingExecutor()
        store = InMemoryReceiptStore()
        receipt = await execute_payment(
            signed_intent,
            signed_cart,
            user_public_key=user_pk,
            merchant_public_key=merchant_pk,
            executor=executor,
            payment_method_ref="pm_test_1",
            receipt_store=store,
            replay_guard=InMemoryReplayGuard(),
        )
        assert receipt.status == "captured"
        assert receipt.amount_cents == 8000
        assert executor.calls == [(signed_cart.mandate, "pm_test_1")]
        assert await store.get(receipt.transaction_id) == receipt
        assert await store.list_for_intent(receipt.intent_id) == [receipt]
        executed = [
            e
            for e in audit_sink.events
            if e.event_type == AuditEventType.PAYMENT_EXECUTED
        ]
        assert len(executed) == 1
        event = executed[0]
        assert event.success is True
        assert event.resource == "merchant-1"
        assert event.action == receipt.intent_id
        assert event.details["transaction_id"] == receipt.transaction_id
        assert event.details["amount_cents"] == 8000
        assert event.details["status"] == "captured"
        assert event.details["psp"] == "testpsp"

    async def test_declined_receipt_audits_failure_but_is_recorded(
        self, audit_sink: _RecordingSink
    ) -> None:
        signed_intent, signed_cart, user_pk, merchant_pk = _chain()
        executor = _RecordingExecutor(status="declined")
        store = InMemoryReceiptStore()
        receipt = await execute_payment(
            signed_intent,
            signed_cart,
            user_public_key=user_pk,
            merchant_public_key=merchant_pk,
            executor=executor,
            payment_method_ref="pm_test_1",
            receipt_store=store,
            replay_guard=InMemoryReplayGuard(),
        )
        assert receipt.status == "declined"
        assert await store.get(receipt.transaction_id) == receipt
        failed = [
            e
            for e in audit_sink.events
            if e.event_type == AuditEventType.PAYMENT_FAILED
        ]
        assert len(failed) == 1
        assert failed[0].success is False
        assert failed[0].details["status"] == "declined"
        assert not [
            e
            for e in audit_sink.events
            if e.event_type == AuditEventType.PAYMENT_EXECUTED
        ]

    async def test_executor_exception_records_error_receipt_and_chains(
        self, audit_sink: _RecordingSink
    ) -> None:
        signed_intent, signed_cart, user_pk, merchant_pk = _chain()
        executor = _RaisingExecutor()
        store = InMemoryReceiptStore()
        with pytest.raises(PaymentExecutionError) as exc_info:
            await execute_payment(
                signed_intent,
                signed_cart,
                user_public_key=user_pk,
                merchant_public_key=merchant_pk,
                executor=executor,
                payment_method_ref="pm_test_1",
                receipt_store=store,
                replay_guard=InMemoryReplayGuard(),
            )
        assert isinstance(exc_info.value.__cause__, ConnectionError)
        recorded = await store.list_for_intent(signed_cart.mandate.intent_id)
        assert len(recorded) == 1
        assert recorded[0].status == "error"
        assert recorded[0].psp == "brokenpsp"
        assert "ConnectionError" in recorded[0].detail
        failed = [
            e
            for e in audit_sink.events
            if e.event_type == AuditEventType.PAYMENT_FAILED
        ]
        assert len(failed) == 1
        assert failed[0].success is False
        assert failed[0].details["status"] == "error"

    async def test_failed_chain_never_calls_executor(
        self, audit_sink: _RecordingSink
    ) -> None:
        # Cart total exceeds the intent cap: verify_chain must reject before
        # the executor is ever consulted, and nothing may be recorded.
        signed_intent, signed_cart, user_pk, merchant_pk = _chain(
            max_price=100.0, price=150.0
        )
        executor = _RecordingExecutor()
        store = InMemoryReceiptStore()
        with pytest.raises(MandateChainError):
            await execute_payment(
                signed_intent,
                signed_cart,
                user_public_key=user_pk,
                merchant_public_key=merchant_pk,
                executor=executor,
                payment_method_ref="pm_test_1",
                receipt_store=store,
                replay_guard=InMemoryReplayGuard(),
            )
        assert executor.calls == []
        assert await store.list_for_intent(signed_cart.mandate.intent_id) == []
        assert not [
            e
            for e in audit_sink.events
            if e.event_type
            in (AuditEventType.PAYMENT_EXECUTED, AuditEventType.PAYMENT_FAILED)
        ]

    async def test_tampered_signature_propagates_unchanged(self) -> None:
        signed_intent, signed_cart, user_pk, _ = _chain()
        attacker = Ed25519PrivateKey.generate()
        executor = _RecordingExecutor()
        with pytest.raises(MandateSignatureError):
            await execute_payment(
                signed_intent,
                signed_cart,
                user_public_key=user_pk,
                merchant_public_key=attacker.public_key(),
                executor=executor,
                payment_method_ref="pm_test_1",
                replay_guard=InMemoryReplayGuard(),
            )
        assert executor.calls == []

    async def test_replay_raises_with_no_second_charge(
        self, audit_sink: _RecordingSink
    ) -> None:
        signed_intent, signed_cart, user_pk, merchant_pk = _chain()
        executor = _RecordingExecutor()
        store = InMemoryReceiptStore()
        guard = InMemoryReplayGuard()
        kwargs: dict[str, Any] = dict(
            user_public_key=user_pk,
            merchant_public_key=merchant_pk,
            executor=executor,
            payment_method_ref="pm_test_1",
            receipt_store=store,
            replay_guard=guard,
        )
        first = await execute_payment(signed_intent, signed_cart, **kwargs)
        assert first.status == "captured"
        with pytest.raises(MandateReplayError):
            await execute_payment(signed_intent, signed_cart, **kwargs)
        assert len(executor.calls) == 1
        assert len(await store.list_for_intent(first.intent_id)) == 1
