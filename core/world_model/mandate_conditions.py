"""Evaluation of signed ``IntentMandate.conditions`` against a cart.

The user signs ``conditions`` into the intent envelope, so ignoring them
silently widens the authorization beyond what was granted — the verifier
must either enforce a condition or reject the chain. Unknown condition
keys therefore **fail closed**: a signed constraint this verifier cannot
evaluate is reported as a violation, never skipped.

Supported conditions:

- ``allowed_merchants`` (list[str]): ``cart.merchant_id`` must be listed.
- ``max_items`` (int): total cart quantity across all lines must not
  exceed it.
- ``allowed_skus`` (list[str]): every cart line's SKU must be listed.

Enforcement inside :func:`core.world_model.mandates.verify_chain` is on by
default; ``BASELITH_AP2_ENFORCE_CONDITIONS=0`` is the kill-switch.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from core.utils.logsafe import sanitize_log_value

if TYPE_CHECKING:
    from core.world_model.mandates import CartMandate, IntentMandate

_KNOWN_CONDITIONS = frozenset({"allowed_merchants", "max_items", "allowed_skus"})


def conditions_enforced() -> bool:
    """Whether verify_chain evaluates intent conditions (kill-switch aware)."""
    return os.getenv("BASELITH_AP2_ENFORCE_CONDITIONS", "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def evaluate_intent_conditions(intent: IntentMandate, cart: CartMandate) -> list[str]:
    """Check ``cart`` against the intent's signed conditions.

    Args:
        intent: The user-signed intent whose ``conditions`` to enforce.
        cart: The merchant-signed cart under verification.

    Returns:
        Human-readable violation descriptions; empty when the cart conforms.
        Peer-supplied values are sanitized before inclusion so a crafted
        merchant id or SKU cannot forge log lines downstream.
    """
    violations: list[str] = []
    for key, expected in intent.conditions.items():
        if key == "allowed_merchants":
            allowed = _as_str_list(expected)
            if allowed is None:
                violations.append("allowed_merchants condition is malformed")
            elif cart.merchant_id not in allowed:
                violations.append(
                    "merchant "
                    f"{sanitize_log_value(cart.merchant_id)} is not in the "
                    "intent's allowed_merchants"
                )
        elif key == "max_items":
            if not isinstance(expected, int) or expected < 1:
                violations.append("max_items condition is malformed")
            else:
                total = sum(item.quantity for item in cart.items)
                if total > expected:
                    violations.append(
                        f"cart holds {total} items, exceeding max_items {expected}"
                    )
        elif key == "allowed_skus":
            allowed = _as_str_list(expected)
            if allowed is None:
                violations.append("allowed_skus condition is malformed")
            else:
                for item in cart.items:
                    if item.sku not in allowed:
                        violations.append(
                            f"sku {sanitize_log_value(item.sku)} is not in "
                            "the intent's allowed_skus"
                        )
        else:
            # Fail closed: a signed condition this verifier cannot evaluate
            # must not silently widen the authorization.
            violations.append(
                f"unsupported signed condition {sanitize_log_value(key)} "
                "cannot be evaluated"
            )
    return violations


def _as_str_list(value: Any) -> list[str] | None:
    """Return ``value`` as a list of strings, or None when malformed."""
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    return None


__all__ = ["conditions_enforced", "evaluate_intent_conditions"]
