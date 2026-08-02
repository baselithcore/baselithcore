"""Selector hygiene and vision-payload → :class:`BrowserAction` mapping."""

from __future__ import annotations

import re
from typing import Any

from .types import BrowserAction, BrowserActionType

_JQUERY_CONTAINS = re.compile(r":contains\(\s*['\"]([^'\"]+)['\"]\s*\)")


def normalize_selector(selector: str) -> str:
    """Translate jQuery-style ``:contains("X")`` to Playwright ``:has-text("X")``.

    Vision models frequently emit jQuery-flavored selectors that Playwright's
    query engine rejects. Rewriting here keeps the click/fill call sites free
    of model-specific quirks.

    Args:
        selector: Raw selector as emitted by the vision model.

    Returns:
        The selector with jQuery-only pseudo-classes rewritten.
    """
    return _JQUERY_CONTAINS.sub(lambda m: f':has-text("{m.group(1)}")', selector)


def build_action(
    action_type: BrowserActionType, payload: dict[str, Any]
) -> BrowserAction:
    """Build a :class:`BrowserAction` from a decoded vision JSON payload.

    Models are loose about where they put their argument: ``value`` may hold a
    scalar, a dict or a list, the URL may arrive under ``url``, and structured
    output may arrive under ``data``. All shapes are folded into the action's
    ``value``/``data`` fields here.

    Args:
        action_type: Already-validated action type from ``payload["action"]``.
        payload: Decoded JSON object returned by the vision model.

    Returns:
        The action to execute next.
    """
    raw_value = payload.get("value")
    value_str: str | None = None
    data_payload: dict[str, Any] | None = None
    if isinstance(raw_value, dict):
        data_payload = raw_value
    elif isinstance(raw_value, list):
        data_payload = {"items": raw_value}
    elif raw_value is not None:
        value_str = str(raw_value)
    if value_str is None:
        url_val = payload.get("url")
        if url_val is not None:
            value_str = str(url_val)
    explicit_data = payload.get("data")
    if isinstance(explicit_data, dict):
        data_payload = (
            {**(data_payload or {}), **explicit_data} if data_payload else explicit_data
        )

    return BrowserAction(
        action_type=action_type,
        selector=payload.get("selector"),
        value=value_str,
        coordinates=tuple(payload["coordinates"]) if "coordinates" in payload else None,
        reasoning=payload.get("reasoning") or payload.get("explanation") or "",
        data=data_payload,
    )


__all__ = ["build_action", "normalize_selector"]
