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

A decision is also bound to the **visit** it was made for: the scope the gate ran
in (a workflow ITERATION item or subgraph has its own) and the occurrence of
that ``(scope, tool, category, digest)`` gate within the pass. Without it one
approval covered every later identical call, and a HUMAN gate approved on the
first item of a list passed on all the others; a deliberate repeat is a new
call and asks again. Occurrences are counted per pass, like the content-
addressed step keys, so a resumed run replays its approved visits in order and
pauses at the first one nobody decided.

A request with no digest keeps the old contract: it matches by name and
category. That is what callers that never pass arguments rely on (the HUMAN
workflow node, plugin gates) and what a run paused by an earlier release holds.
"""

from __future__ import annotations

import time
from typing import Any, Final

__all__ = [
    "MAX_DECIDED",
    "MAX_PREVIEW_CHARS",
    "GateVisits",
    "args_preview",
    "decision_for",
    "new_pending_request",
]

#: Characters of the redacted argument preview kept on a pending request.
MAX_PREVIEW_CHARS: Final = 1000
#: Longest single string value shown. Cut per value, before the whole preview
#: is cut: a long benign field that sorts first must not push the recipient or
#: the amount out of what the reviewer reads.
_MAX_VALUE_CHARS: Final = 160

#: Earlier bound decisions kept on the slot. A run that needs more than this many
#: distinct approvals asks again for the oldest one on replay, which is safe.
MAX_DECIDED: Final = 64


class GateVisits:
    """Per-pass count of visits to each approval gate.

    ``visit`` draws the occurrence for a gate being evaluated; ``claim`` hands
    the same number to the pause that follows in the same evaluation, so the
    request and the decision are bound to one visit.
    """

    def __init__(self) -> None:
        self._next: dict[tuple[Any, ...], int] = {}
        self._open: dict[tuple[Any, ...], int] = {}

    def visit(self, key: tuple[Any, ...]) -> int:
        occurrence = self._next.get(key, 0)
        self._next[key] = occurrence + 1
        self._open[key] = occurrence
        return occurrence

    def claim(self, key: tuple[Any, ...]) -> int:
        if key in self._open:
            return self._open.pop(key)
        occurrence = self._next.get(key, 0)
        self._next[key] = occurrence + 1
        return occurrence


def _shorten(value: Any, depth: int = 0) -> Any:
    """``value`` with every long string cut, so each field stays visible."""
    if isinstance(value, str):
        return (
            value if len(value) <= _MAX_VALUE_CHARS else value[:_MAX_VALUE_CHARS] + "…"
        )
    if depth >= 6:
        return "…"
    if isinstance(value, dict):
        return {k: _shorten(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_shorten(v, depth + 1) for v in value[:50]]
    return value


def args_preview(args: Any) -> str | None:
    """What the reviewer is shown of a call's arguments: redacted, bounded.

    The digest identifies the call; a hash alone asks the reviewer to approve
    something they cannot read. Credentials and e-mail local parts are masked
    (the stored preview outlives the call), and the text is cut at
    :data:`MAX_PREVIEW_CHARS`.
    """
    if args is None:
        return None
    from baselith.core.observability.redaction import redact_text
    from baselith.core.orchestration.call_keys import canonical_arguments

    text = redact_text(canonical_arguments(_shorten(args)))
    if len(text) > MAX_PREVIEW_CHARS:
        text = text[:MAX_PREVIEW_CHARS] + "…"
    return text


def _same_visit(entry: dict[str, Any], scope: str, occurrence: int | None) -> bool:
    """Whether ``entry`` was recorded for this visit.

    A key the entry lacks (a run paused by an earlier release) matches any
    visit: that one in-flight pause keeps the contract it was made under.
    """
    return entry.get("scope", scope) == scope and entry.get(
        "occurrence", occurrence
    ) in (occurrence, None)


def _carried_decisions(previous: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Decisions of earlier bound requests, with the replaced request's added."""
    if not previous:
        return []
    decided: list[dict[str, Any]] = list(previous.get("decided") or [])
    digest = previous.get("args_digest")
    decision = previous.get("decision")
    if digest is not None and isinstance(decision, dict):
        visit = (previous.get("scope"), previous.get("occurrence"))
        key = (previous.get("tool_name"), previous.get("category"), digest, *visit)
        decided = [
            entry
            for entry in decided
            if (
                entry.get("tool_name"),
                entry.get("category"),
                entry.get("args_digest"),
                entry.get("scope"),
                entry.get("occurrence"),
            )
            != key
        ]
        decided.append(
            {
                "tool_name": previous.get("tool_name"),
                "category": previous.get("category"),
                "args_digest": digest,
                "scope": previous.get("scope"),
                "occurrence": previous.get("occurrence"),
                "approved": bool(decision.get("approved")),
            }
        )
    return decided[-MAX_DECIDED:]


def new_pending_request(
    tool_name: str,
    category: str,
    args_digest: str | None,
    previous: dict[str, Any] | None,
    *,
    scope: str = "",
    occurrence: int = 0,
    preview: str | None = None,
) -> dict[str, Any]:
    """The ``pending_approval`` payload for a new request, history carried over."""
    pending: dict[str, Any] = {
        "tool_name": tool_name,
        "category": category,
        "scope": scope,
        "occurrence": occurrence,
        "requested_at": time.time(),
    }
    if args_digest is not None:
        pending["args_digest"] = args_digest
    if preview is not None:
        pending["args_preview"] = preview
    decided = _carried_decisions(previous)
    if decided:
        pending["decided"] = decided
    return pending


def decision_for(
    pending: dict[str, Any] | None,
    tool_name: str,
    category: str,
    args_digest: str | None,
    *,
    scope: str = "",
    occurrence: int | None = None,
) -> bool | None:
    """The recorded verdict for this visit: True, False, or None when there is none."""
    if not pending:
        return None
    if (
        "decision" in pending
        and pending.get("tool_name") == tool_name
        and pending.get("category") == category
        and _same_visit(pending, scope, occurrence)
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
                and _same_visit(entry, scope, occurrence)
            ):
                return bool(entry.get("approved"))
    return None
