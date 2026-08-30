"""Delegated (human-not-present) purchase mode for the AP2 vertical.

The user pre-signs one or more ``IntentMandate`` envelopes while present;
a :class:`DelegatedPurchaseAgent` later evaluates incoming merchant offers
against them with nobody watching. The agent is deliberately thin: every
check it performs up front is a zero-side-effect pre-flight, and the actual
purchase always goes through
:func:`core.world_model.payments.execute_payment`, which re-verifies the
full chain and consumes the intent through the replay guard — the agent
has no path around the protocol. One intent authorizes at most one
purchase.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import cast

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from core.world_model.mandate_conditions import evaluate_intent_conditions
from core.world_model.mandates import (
    _USE_DEFAULT_GUARD,
    CartMandate,
    IntentMandate,
    MandateChainError,
    MandateReplayError,
    ReplayGuard,
    SignedMandate,
    _usd_to_cents,
    _UseDefaultGuard,
    verify_signature,
)
from core.world_model.payments import (
    PaymentExecutor,
    PaymentReceipt,
    ReceiptStore,
    execute_payment,
)


class DelegationRegistryFullError(RuntimeError):
    """Raised when registering an intent beyond the agent's bounded capacity."""


class DelegatedPurchaseAgent:
    """Autonomous buyer for pre-signed intents (human-not-present mode).

    Usage: the user signs intents while present and hands them to
    :meth:`register`; merchants later present signed carts to
    :meth:`evaluate_offer`, which either declines silently (returns None,
    zero side effects) or purchases through the full
    :func:`~core.world_model.payments.execute_payment` seam.

    Args:
        executor: PSP adapter that performs the actual charge.
        user_public_key_resolver: Maps an intent's ``user_id`` to the
            Ed25519 public key its signature must verify against. Called at
            registration *and* again at execution — the agent never caches
            trust decisions past a key rotation.
        receipt_store: Optional store every produced receipt is recorded in.
        replay_guard: Single-use intent ledger passed through to
            ``execute_payment``; defaults to the deployment default.
        max_intents: Upper bound on concurrently registered intents.
            Registration past it raises :class:`DelegationRegistryFullError`
            (after pruning expired entries).
    """

    def __init__(
        self,
        executor: PaymentExecutor,
        *,
        user_public_key_resolver: Callable[[str], Ed25519PublicKey],
        receipt_store: ReceiptStore | None = None,
        replay_guard: ReplayGuard | None | _UseDefaultGuard = _USE_DEFAULT_GUARD,
        max_intents: int = 1000,
    ) -> None:
        self._executor = executor
        self._resolve_key = user_public_key_resolver
        self._receipt_store = receipt_store
        self._replay_guard = replay_guard
        self._max_intents = max(1, max_intents)
        self._intents: dict[str, SignedMandate] = {}

    def __len__(self) -> int:
        """Number of currently registered intents."""
        return len(self._intents)

    def register(self, signed_intent: SignedMandate) -> None:
        """Register a pre-signed intent for later autonomous purchase.

        The user signature is verified immediately against the resolver's
        key for ``intent.user_id`` — an invalid or expired envelope never
        enters the registry.

        Args:
            signed_intent: User-signed :class:`IntentMandate`.

        Raises:
            MandateChainError: Not an ``IntentMandate``, or already expired.
            MandateSignatureError: The user signature does not verify.
            DelegationRegistryFullError: The bounded registry is full.
        """
        intent = signed_intent.mandate
        if not isinstance(intent, IntentMandate):
            raise MandateChainError("delegated registration requires an IntentMandate")
        verify_signature(signed_intent, self._resolve_key(intent.user_id))
        current = time.time()
        if current >= intent.expires_at:
            raise MandateChainError(
                f"intent expired at {intent.expires_at}, now {current}"
            )
        if intent.intent_id not in self._intents and (
            len(self._intents) >= self._max_intents
        ):
            self._prune_expired(current)
            if len(self._intents) >= self._max_intents:
                raise DelegationRegistryFullError(
                    f"delegated intent registry is full ({self._max_intents})"
                )
        self._intents[intent.intent_id] = signed_intent

    async def evaluate_offer(
        self,
        signed_cart: SignedMandate,
        *,
        merchant_public_key: Ed25519PublicKey,
        payment_method_ref: str,
        now: float | None = None,
    ) -> PaymentReceipt | None:
        """Evaluate a merchant offer against the registered intents.

        Returns None — with zero side effects — when the cart references no
        registered intent, the intent has expired, the cart violates the
        intent's signed conditions, or the total exceeds the authorized cap.
        Otherwise the purchase is delegated to
        :func:`~core.world_model.payments.execute_payment`, which re-verifies
        everything and consumes the intent exactly once; a replayed intent
        also resolves to None rather than a second charge.

        With no human present, the signed conditions are always honored here
        regardless of the ``BASELITH_AP2_ENFORCE_CONDITIONS`` kill-switch:
        there is nobody around to notice a silently widened authorization.

        Args:
            signed_cart: Merchant-signed :class:`CartMandate`.
            merchant_public_key: Key the cart must verify against.
            payment_method_ref: Opaque payment-instrument token for the PSP.
            now: Time override (testing).

        Returns:
            The :class:`PaymentReceipt` when a purchase was attempted (it may
            still be ``"declined"``), or None when the offer was passed over.

        Raises:
            MandateChainError: ``signed_cart`` does not wrap a
                ``CartMandate``, or the full verification inside
                ``execute_payment`` rejected the chain.
            MandateSignatureError: A signature failed verification.
            PaymentExecutionError: The PSP executor itself failed.
        """
        cart = signed_cart.mandate
        if not isinstance(cart, CartMandate):
            raise MandateChainError("delegated offer evaluation requires a CartMandate")
        registered = self._intents.get(cart.intent_id)
        if registered is None:
            return None
        intent = cast(IntentMandate, registered.mandate)
        current = now if now is not None else time.time()
        if current >= intent.expires_at:
            # Useless forever — free the slot.
            self._intents.pop(cart.intent_id, None)
            return None
        # Zero-side-effect pre-flight: nothing below may consume the intent.
        if cart.total_cents() > _usd_to_cents(intent.max_price_usd):
            return None
        if intent.conditions and evaluate_intent_conditions(intent, cart):
            return None
        try:
            receipt = await execute_payment(
                registered,
                signed_cart,
                user_public_key=self._resolve_key(intent.user_id),
                merchant_public_key=merchant_public_key,
                executor=self._executor,
                payment_method_ref=payment_method_ref,
                receipt_store=self._receipt_store,
                replay_guard=self._replay_guard,
                now=now,
            )
        except MandateReplayError:
            # Already consumed (e.g. an earlier declined attempt burned it):
            # one intent buys at most once, so this is a pass, not an error.
            self._intents.pop(cart.intent_id, None)
            return None
        if receipt.status == "captured":
            # One intent = max one purchase; the replay guard already
            # enforces it, dropping the entry just keeps the registry clean.
            self._intents.pop(cart.intent_id, None)
        return receipt

    def _prune_expired(self, current: float) -> None:
        """Drop every registered intent that has already expired."""
        expired = [
            intent_id
            for intent_id, signed in self._intents.items()
            if current >= cast(IntentMandate, signed.mandate).expires_at
        ]
        for intent_id in expired:
            del self._intents[intent_id]


__all__ = [
    "DelegatedPurchaseAgent",
    "DelegationRegistryFullError",
]
