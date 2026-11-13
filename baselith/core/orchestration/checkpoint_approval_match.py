"""Matching a durable approval to the call it was requested for.

A paused run keeps one ``pending_approval`` slot on its checkpoint. Matched on
``(tool name, category)`` alone, a reviewer's decision for one call authorised
every later call of that tool in the run, whatever its arguments, because
nothing consumed or cleared it. The request now optionally carries
``args_digest``, the SHA-256 of the call's canonicalised arguments (the digest
the audit trail records), and a decision matches only the digest it was
recorded for.

A run can pause several times, and a resumed run replays its earlier calls
before it reaches the one that paused. Overwriting the slot would make that
replay ask again for a call already approved, so the decisions of earlier bound
requests are carried forward under ``decided`` (newest kept, bounded).

A request with no digest keeps the old contract: it matches by name and
category. That is what callers that never pass arguments rely on (the HUMAN
workflow node, plugin gates) and what a run paused by an earlier release holds.
"""

from __future__ import annotations

import time
from typing import Any, Final

__all__ = ["MAX_DECIDED", "decision_for", "new_pending_request"]

#: Earlier bound decisions kept on the slot. A run that needs more than this many
#: distinct approvals asks again for the oldest one on replay, which is safe.
MAX_DECIDED: Final = 64


def _carried_decisions(previous: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Decisions of earlier bound requests, with the replaced request's added."""
    if not previous:
        return []
    decided: list[dict[str, Any]] = list(previous.get("decided") or [])
    digest = previous.get("args_digest")
    decision = previous.get("decision")
    if digest is not None and isinstance(decision, dict):
        key = (previous.get("tool_name"), previous.get("category"), digest)
        decided = [
            entry
            for entry in decided
            if (entry.get("tool_name"), entry.get("category"), entry.get("args_digest"))
            != key
        ]
        decided.append(
            {
                "tool_name": previous.get("tool_name"),
                "category": previous.get("category"),
                "args_digest": digest,
                "approved": bool(decision.get("approved")),
            }
        )
    return decided[-MAX_DECIDED:]


def new_pending_request(
    tool_name: str,
    category: str,
    args_digest: str | None,
    previous: dict[str, Any] | None,
) -> dict[str, Any]:
    """The ``pending_approval`` payload for a new request, history carried over."""
    pending: dict[str, Any] = {
        "tool_name": tool_name,
        "category": category,
        "requested_at": time.time(),
    }
    if args_digest is not None:
        pending["args_digest"] = args_digest
    decided = _carried_decisions(previous)
    if decided:
        pending["decided"] = decided
    return pending


def decision_for(
    pending: dict[str, Any] | None,
    tool_name: str,
    category: str,
    args_digest: str | None,
) -> bool | None:
    """The recorded verdict for this call: True, False, or None when there is none."""
    if not pending:
        return None
    if (
        "decision" in pending
        and pending.get("tool_name") == tool_name
        and pending.get("category") == category
    ):
        bound = pending.get("args_digest")
        if bound is None or bound == args_digest:
            return bool(pending["decision"].get("approved"))
    if args_digest is not None:
        for entry in reversed(pending.get("decided") or ()):
            if (
                entry.get("tool_name") == tool_name
                and entry.get("category") == category
                and entry.get("args_digest") == args_digest
            ):
                return bool(entry.get("approved"))
    return None
