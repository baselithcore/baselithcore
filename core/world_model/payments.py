"""AP2 payment execution: the seam between a verified mandate chain and a PSP.

``core.world_model.mandates`` owns the *protocol* — signatures, the
cart-vs-intent envelope, replay consumption — and explicitly leaves
"execution of the purchase" elsewhere. This module is that elsewhere:
:func:`execute_payment` re-verifies the full chain (consuming the intent
exactly once through the replay guard), hands the verified cart to a
:class:`PaymentExecutor` (a PSP adapter, which lives in a plugin), records
a non-repudiable :class:`PaymentReceipt`, and emits ``payment.executed`` /
``payment.failed`` audit events for every outcome.

Executors receive the **already-verified** cart plus an opaque
``payment_method_ref`` token — they never see mandate keys, signatures, or
raw payment credentials.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Literal, Protocol, cast, runtime_checkable

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from core.observability import audit as _audit
from core.observability.audit import AuditEventType
from core.observability.logging import get_logger
from core.utils.logsafe import sanitize_log_value
from core.world_model.mandates import (
    _USE_DEFAULT_GUARD,
    CartMandate,
    IntentMandate,
    ReplayGuard,
    SignedMandate,
    _UseDefaultGuard,
    verify_chain,
)

logger = get_logger(__name__)

PaymentStatus = Literal["captured", "declined", "error"]


class PaymentExecutionError(RuntimeError):
    """Raised when the PSP executor itself fails (network, gateway, bug).

    Distinct from the mandate errors: the chain verified — and the intent was
    consumed — but the charge could not complete. The original executor
    exception is chained as ``__cause__``, and an ``"error"`` receipt is
    recorded before this is raised (when a store is available).
    """


@dataclass(frozen=True, slots=True)
class PaymentReceipt:
    """Non-repudiable record of one payment attempt against a mandate chain.

    Attributes:
        transaction_id: PSP-assigned identifier for the attempt.
        intent_id: The user-signed intent the purchase was authorized by.
        cart_id: The merchant-signed cart that was charged.
        merchant_id: Merchant the cart was pinned to.
        amount_cents: Amount in integer cents — the unit all money math
            runs in (see ``CartMandate.total_cents``).
        status: ``"captured"``, ``"declined"``, or ``"error"``.
        executed_at: Unix timestamp of the attempt.
        psp: Name of the payment service provider adapter used.
        currency: ISO 4217 code; the AP2 chain runs in USD today.
        detail: Optional human-readable outcome detail.
    """

    transaction_id: str
    intent_id: str
    cart_id: str
    merchant_id: str
    amount_cents: int
    status: PaymentStatus
    executed_at: float
    psp: str
    currency: str = "USD"
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return the receipt as a plain dictionary."""
        return asdict(self)


@runtime_checkable
class PaymentExecutor(Protocol):
    """The PSP seam: one async call turning a verified cart into a receipt.

    Implementations (Stripe, Adyen, the reference mock in
    ``plugins/payments``) receive the **already-verified** cart — they must
    not re-derive authorization, and they never see keys. A *declined*
    charge is a ``"declined"`` receipt, not an exception; exceptions are
    reserved for infrastructure failures.
    """

    async def execute(
        self, cart: CartMandate, *, payment_method_ref: str
    ) -> PaymentReceipt:
        """Charge ``payment_method_ref`` for ``cart`` and return the receipt."""
        ...


@runtime_checkable
class ReceiptStore(Protocol):
    """Durable, append-only home for :class:`PaymentReceipt` records."""

    async def record(self, receipt: PaymentReceipt) -> None:
        """Persist ``receipt``. Must be idempotent per ``transaction_id``."""
        ...

    async def get(self, transaction_id: str) -> PaymentReceipt | None:
        """Return the receipt for ``transaction_id``, or None."""
        ...

    async def list_for_intent(self, intent_id: str) -> list[PaymentReceipt]:
        """Return every receipt recorded against ``intent_id``."""
        ...


class InMemoryReceiptStore:
    """Process-local :class:`ReceiptStore` for single-process runs and tests.

    Does not survive restarts; production deployments should persist
    receipts durably (they are the non-repudiation evidence).
    """

    def __init__(self) -> None:
        self._by_transaction: dict[str, PaymentReceipt] = {}
        self._by_intent: dict[str, list[PaymentReceipt]] = {}

    async def record(self, receipt: PaymentReceipt) -> None:
        """Persist ``receipt``; a duplicate ``transaction_id`` is a no-op.

        A receipt is immutable evidence: first write wins, re-recording the
        same transaction never rewrites history.
        """
        if receipt.transaction_id in self._by_transaction:
            return
        self._by_transaction[receipt.transaction_id] = receipt
        self._by_intent.setdefault(receipt.intent_id, []).append(receipt)

    async def get(self, transaction_id: str) -> PaymentReceipt | None:
        """Return the receipt for ``transaction_id``, or None."""
        return self._by_transaction.get(transaction_id)

    async def list_for_intent(self, intent_id: str) -> list[PaymentReceipt]:
        """Return every receipt recorded against ``intent_id`` (a copy)."""
        return list(self._by_intent.get(intent_id, []))


def _executor_psp_name(executor: PaymentExecutor) -> str:
    """Best-effort PSP name for an executor that failed before returning."""
    name = getattr(executor, "psp_name", None)
    if isinstance(name, str) and name:
        return name
    return type(executor).__name__


async def _emit_payment_audit(receipt: PaymentReceipt, *, user_id: str) -> None:
    """Emit ``payment.executed`` / ``payment.failed`` for ``receipt``."""
    success = receipt.status == "captured"
    event_type = (
        AuditEventType.PAYMENT_EXECUTED if success else AuditEventType.PAYMENT_FAILED
    )
    # Looked up through the module so a reconfigured global logger (or a test
    # double) is always the one that records the event.
    await _audit.get_audit_logger().log(
        event_type,
        user_id=user_id,
        resource=receipt.merchant_id,
        action=receipt.intent_id,
        details={
            "transaction_id": receipt.transaction_id,
            "amount_cents": receipt.amount_cents,
            "status": receipt.status,
            "psp": receipt.psp,
        },
        success=success,
    )


async def execute_payment(
    signed_intent: SignedMandate,
    signed_cart: SignedMandate,
    *,
    user_public_key: Ed25519PublicKey,
    merchant_public_key: Ed25519PublicKey,
    executor: PaymentExecutor,
    payment_method_ref: str,
    receipt_store: ReceiptStore | None = None,
    replay_guard: ReplayGuard | None | _UseDefaultGuard = _USE_DEFAULT_GUARD,
    expected_merchant_id: str | None = None,
    max_cart_age_seconds: float | None = None,
    now: float | None = None,
) -> PaymentReceipt:
    """Verify the mandate chain, execute the charge, and record the receipt.

    The orchestration is strictly ordered: :func:`verify_chain` runs first
    with the exact arguments given here — consuming the intent through the
    replay guard, so a chain executes at most once — and only a fully
    verified chain ever reaches the executor. A rejected chain propagates
    its ``MandateError`` unchanged, with no executor call and no receipt.

    Args:
        signed_intent: User-signed :class:`IntentMandate`.
        signed_cart: Merchant-signed :class:`CartMandate` pinned to it.
        user_public_key: Key the intent was signed with.
        merchant_public_key: Key the cart was signed with.
        executor: PSP adapter that performs the actual charge.
        payment_method_ref: Opaque payment-instrument token the PSP resolves;
            never a raw credential.
        receipt_store: When given, every produced receipt (including the
            ``"error"`` receipt for an executor crash) is recorded in it.
        replay_guard: Passed through to :func:`verify_chain`; defaults to the
            deployment's default single-use ledger.
        expected_merchant_id: Passed through to :func:`verify_chain`.
        max_cart_age_seconds: Passed through to :func:`verify_chain`.
        now: Time override for verification and the error-receipt timestamp
            (testing).

    Returns:
        The :class:`PaymentReceipt` the executor produced (``"captured"``
        or ``"declined"`` — a decline is an outcome, not an exception).

    Raises:
        MandateSignatureError: A signature failed verification.
        MandateChainError: A cart-vs-intent rule was violated.
        MandateReplayError: The intent had already been consumed.
        PaymentExecutionError: The executor itself failed; the original
            exception is chained, and an ``"error"`` receipt was recorded
            when a store was available.
    """
    verify_chain(
        signed_intent,
        signed_cart,
        user_public_key=user_public_key,
        merchant_public_key=merchant_public_key,
        now=now,
        replay_guard=replay_guard,
        expected_merchant_id=expected_merchant_id,
        max_cart_age_seconds=max_cart_age_seconds,
    )
    # verify_chain guarantees the mandate types; narrow for the type checker.
    intent = cast(IntentMandate, signed_intent.mandate)
    cart = cast(CartMandate, signed_cart.mandate)
    try:
        receipt = await executor.execute(cart, payment_method_ref=payment_method_ref)
    except Exception as exc:
        error_receipt = PaymentReceipt(
            transaction_id=f"err_{uuid.uuid4().hex}",
            intent_id=cart.intent_id,
            cart_id=cart.cart_id,
            merchant_id=cart.merchant_id,
            amount_cents=cart.total_cents(),
            status="error",
            executed_at=now if now is not None else time.time(),
            psp=_executor_psp_name(executor),
            detail=sanitize_log_value(f"{type(exc).__name__}: {exc}"),
        )
        if receipt_store is not None:
            try:
                await receipt_store.record(error_receipt)
            except Exception:
                # Never mask the executor failure with a bookkeeping one.
                logger.exception("failed to record error payment receipt")
        await _emit_payment_audit(error_receipt, user_id=intent.user_id)
        raise PaymentExecutionError(
            f"payment executor failed for intent {sanitize_log_value(cart.intent_id)}"
        ) from exc
    if receipt_store is not None:
        await receipt_store.record(receipt)
    await _emit_payment_audit(receipt, user_id=intent.user_id)
    return receipt


__all__ = [
    "InMemoryReceiptStore",
    "PaymentExecutionError",
    "PaymentExecutor",
    "PaymentReceipt",
    "PaymentStatus",
    "ReceiptStore",
    "execute_payment",
]
