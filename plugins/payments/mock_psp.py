"""Reference PSP adapter for the AP2 payment-execution seam.

``MockPSPAdapter`` is the shape every real PSP adapter (Stripe, Adyen,
PayPal, ...) should follow: implement
:class:`core.world_model.payments.PaymentExecutor` — a single async
``execute`` that receives the **already-verified**
:class:`~core.world_model.mandates.CartMandate` plus an opaque
``payment_method_ref`` token (the PSP resolves it to an instrument; it is
never a PAN and never a key) and returns a
:class:`~core.world_model.payments.PaymentReceipt`. A real adapter holds
its API credentials wrapped in ``pydantic.SecretStr``, returns a
``"declined"`` receipt for a refused charge, and raises only for
infrastructure failures (which ``execute_payment`` converts into an
``"error"`` receipt plus ``PaymentExecutionError``).
"""

from __future__ import annotations

import time
import uuid
from typing import Literal

from core.world_model.mandates import CartMandate
from core.world_model.payments import PaymentReceipt


class MockPSPAdapter:
    """In-memory PSP with deterministic, configurable outcomes.

    Captures every charge whose total is at or under ``decline_over_cents``
    and declines anything above it; with no threshold, everything captures.
    Transaction ids are ``mockpsp_<uuid>``.

    Args:
        decline_over_cents: Cart totals strictly above this (integer cents)
            come back ``"declined"``. ``None`` (default) captures all.
    """

    psp_name = "mockpsp"

    def __init__(self, *, decline_over_cents: int | None = None) -> None:
        self._decline_over_cents = decline_over_cents

    async def execute(
        self, cart: CartMandate, *, payment_method_ref: str
    ) -> PaymentReceipt:
        """Charge the cart total and return a captured or declined receipt.

        Args:
            cart: The already-verified merchant cart.
            payment_method_ref: Opaque instrument token (unused by the mock).

        Returns:
            A ``"captured"`` receipt, or ``"declined"`` when the total
            exceeds the configured threshold.
        """
        total_cents = cart.total_cents()
        declined = (
            self._decline_over_cents is not None
            and total_cents > self._decline_over_cents
        )
        status: Literal["captured", "declined"] = "declined" if declined else "captured"
        detail = (
            f"amount {total_cents} cents exceeds the configured "
            f"decline threshold of {self._decline_over_cents} cents"
            if declined
            else ""
        )
        return PaymentReceipt(
            transaction_id=f"{self.psp_name}_{uuid.uuid4().hex}",
            intent_id=cart.intent_id,
            cart_id=cart.cart_id,
            merchant_id=cart.merchant_id,
            amount_cents=total_cents,
            status=status,
            executed_at=time.time(),
            psp=self.psp_name,
            detail=detail,
        )


__all__ = ["MockPSPAdapter"]
