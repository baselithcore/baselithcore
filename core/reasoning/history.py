"""Deterministic history compaction for agent loops.

A ReAct-style loop appends every thought/action/observation to its
conversation history, so a long run's prompt grows unbounded — cost and
latency rise linearly per iteration and the run eventually overflows the
model's context window. This module bounds that growth **deterministically**
(no extra LLM call, no added cost or prompt-injection surface): when the
history exceeds a token budget, the oldest entries are collapsed to short
head-excerpts while the most recent ``keep_recent`` entries stay intact —
the model keeps its working set, the tail keeps only enough to preserve
narrative continuity.

Budget: ``BASELITH_REACT_HISTORY_MAX_TOKENS`` (default 8000; ``0`` disables
compaction entirely).
"""

from __future__ import annotations

import os

from core.observability.logging import get_logger
from core.utils.tokens import estimate_tokens

logger = get_logger(__name__)

_DEFAULT_MAX_TOKENS = 8000
_COMPACT_HEAD_CHARS = 160
_COMPACT_MARKER = "[compacted]"


def history_token_budget() -> int:
    """Token budget for loop history (0 disables compaction)."""
    raw = os.getenv("BASELITH_REACT_HISTORY_MAX_TOKENS", str(_DEFAULT_MAX_TOKENS))
    try:
        return max(int(raw), 0)
    except ValueError:
        return _DEFAULT_MAX_TOKENS


def _compact_entry(text: str) -> str:
    """Collapse one history entry to a short head excerpt."""
    head = text[:_COMPACT_HEAD_CHARS].rstrip()
    if len(text) <= _COMPACT_HEAD_CHARS:
        return text
    return f"{_COMPACT_MARKER} {head}…"


def compact_history(
    entries: list[str],
    max_tokens: int | None = None,
    *,
    keep_recent: int = 4,
) -> list[str]:
    """Bound *entries* to *max_tokens*, compacting oldest-first.

    The last ``keep_recent`` entries are never compacted (the model's working
    set); older entries are collapsed one at a time, oldest first, until the
    estimated total fits the budget. Idempotent — already-compacted entries
    are skipped. Returns a new list; the input is not mutated.

    Args:
        entries: Ordered history lines (oldest first).
        max_tokens: Token budget; defaults to ``history_token_budget()``.
            ``0`` (or negative) disables compaction.
        keep_recent: Number of newest entries always kept intact.
    """
    budget = history_token_budget() if max_tokens is None else max_tokens
    if budget <= 0 or not entries:
        return list(entries)

    result = list(entries)
    total = sum(estimate_tokens(entry) for entry in result)
    if total <= budget:
        return result

    compacted = 0
    cutoff = max(len(result) - keep_recent, 0)
    for index in range(cutoff):
        if total <= budget:
            break
        entry = result[index]
        if entry.startswith(_COMPACT_MARKER):
            continue
        replacement = _compact_entry(entry)
        if replacement == entry:
            continue
        total -= estimate_tokens(entry) - estimate_tokens(replacement)
        result[index] = replacement
        compacted += 1

    if compacted:
        logger.debug(
            "react_history_compacted entries=%d budget=%d est_tokens=%d",
            compacted,
            budget,
            total,
        )
    return result


def compact_messages(
    messages: list[dict[str, str]],
    max_tokens: int | None = None,
    *,
    keep_recent: int = 4,
) -> list[dict[str, str]]:
    """Message-list variant of :func:`compact_history`.

    The ``system`` message and the first user turn (the task itself) are
    never compacted; the newest ``keep_recent`` messages stay intact. Returns
    a new list with copied (never shared-mutated) message dicts.
    """
    budget = history_token_budget() if max_tokens is None else max_tokens
    if budget <= 0 or not messages:
        return list(messages)

    result = [dict(message) for message in messages]
    total = sum(estimate_tokens(m.get("content", "")) for m in result)
    if total <= budget:
        return result

    first_user_seen = False
    cutoff = max(len(result) - keep_recent, 0)
    for index in range(cutoff):
        if total <= budget:
            break
        message = result[index]
        role = message.get("role", "user")
        if role == "system":
            continue
        if role == "user" and not first_user_seen:
            first_user_seen = True  # the original task — always intact
            continue
        content = message.get("content", "")
        if content.startswith(_COMPACT_MARKER):
            continue
        replacement = _compact_entry(content)
        if replacement == content:
            continue
        total -= estimate_tokens(content) - estimate_tokens(replacement)
        message["content"] = replacement
    return result


__all__ = ["compact_history", "compact_messages", "history_token_budget"]
