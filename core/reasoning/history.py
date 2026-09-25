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

import json
import os
from typing import TYPE_CHECKING, Any

from core.observability.logging import get_logger
from core.utils.tokens import estimate_tokens

if TYPE_CHECKING:  # pragma: no cover - typing only
    from core.services.llm.messages import Message

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


def _shorten(block: Any, field: str, text: str) -> tuple[Any, int]:
    """Collapse one block's text, returning the block and the tokens saved.

    Idempotent, which :func:`compact_history` gets for free by checking the
    marker on the whole entry: a block already carrying ``[compacted]`` is
    left exactly as it is. Without that check a long run compacts the same
    block on every pass, stacking a second marker and eating twelve more
    characters of real content each time.

    Args:
        block: The block to shorten.
        field: Name of its text attribute.
        text: Its current value.

    Returns:
        ``(block, tokens_saved)`` — the original object when nothing changed.
    """
    from dataclasses import replace

    if text.startswith(_COMPACT_MARKER):
        return block, 0
    shorter = _compact_entry(text)
    if shorter == text:
        return block, 0
    saved = estimate_tokens(text) - estimate_tokens(shorter)
    if saved <= 0:
        return block, 0
    return replace(block, **{field: shorter}), saved


def _compact_blocks(message: Message) -> tuple[Message, int]:
    """Return a copy of *message* with its long text collapsed, and the saving.

    Only two block kinds are touched. A ``ToolUseBlock`` carries the call the
    model made and the id its result is correlated by, so shortening it breaks
    the pairing the provider validates. A ``ThinkingBlock`` must be replayed
    byte-for-byte; a turn containing one is skipped whole, because editing its
    neighbouring text can invalidate the signature that travels with it.

    Args:
        message: The message to compact.

    Returns:
        ``(message, tokens_saved)`` — the original object when nothing changed.
    """
    from dataclasses import replace

    from core.services.llm.messages import TextBlock, ThinkingBlock, ToolResultBlock

    if any(isinstance(block, ThinkingBlock) for block in message.content):
        return message, 0

    saved = 0
    blocks = []
    for block in message.content:
        if isinstance(block, TextBlock):
            block, gained = _shorten(block, "text", block.text)
        elif isinstance(block, ToolResultBlock):
            block, gained = _shorten(block, "content", block.content)
        else:
            gained = 0
        saved += gained
        blocks.append(block)

    if saved <= 0:
        # Not merely "nothing to do": re-compacting an already-short excerpt
        # can *grow* the estimate (the marker is itself tokens), and applying
        # that would both re-prefix the text and push the running total the
        # wrong way. The marker guard below makes this unreachable for a block
        # this function wrote; the check keeps it unreachable for any other.
        return message, 0
    return replace(message, content=blocks), saved


def _block_size(block: Any) -> int:
    """Every block's weight, not only the two that are compactable.

    A ``ToolUseBlock``'s arguments and a ``ThinkingBlock``'s payload are
    sent and billed like any other content. Counting them as zero let a
    history dominated by either measure as comfortably under budget and
    never compact at all, which is the case compaction exists for.
    """
    for attribute in ("text", "content"):
        value = getattr(block, attribute, None)
        if isinstance(value, str):
            return estimate_tokens(value)
    for attribute in ("input", "payload"):
        value = getattr(block, attribute, None)
        if isinstance(value, dict):
            return estimate_tokens(json.dumps(value, default=str))
    data = getattr(block, "data", None) or getattr(block, "url", None)
    return estimate_tokens(data) if isinstance(data, str) else 0


def message_token_size(message: Message) -> int:
    """Estimated tokens of one message, every block kind counted.

    Args:
        message: The message to measure.

    Returns:
        The summed estimate over its blocks.
    """
    return sum(_block_size(block) for block in message.content)


def compact_message_history(
    history: list[Message],
    max_tokens: int | None = None,
    *,
    keep_recent: int = 4,
    protect_head: int = 1,
) -> list[Message]:
    """Bound a :class:`~core.services.llm.messages.Message` history.

    The structural counterpart to :func:`compact_history`. A message loop
    cannot be bounded by dropping turns: a provider rejects a conversation
    whose ``tool_use`` has no answering ``tool_result``, and the first user
    turn is the task itself. So nothing is ever removed — the *contents* of
    older text and tool-result blocks are collapsed to head excerpts, which
    shrinks the prompt while leaving every id, every pairing and every
    thinking block exactly where the API expects them.

    Args:
        history: Messages, oldest first.
        max_tokens: Token budget; defaults to :func:`history_token_budget`.
            ``0`` (or negative) disables compaction.
        keep_recent: Number of newest messages always kept intact.
        protect_head: Number of oldest messages never compacted — ``1``
            keeps the task; the summarised path passes ``2`` so the summary
            turn after it is not cut down to an excerpt.

    Returns:
        A new list; the input and its messages are not mutated.
    """
    budget = history_token_budget() if max_tokens is None else max_tokens
    if budget <= 0 or not history:
        return list(history)

    result = list(history)
    total = sum(message_token_size(message) for message in result)
    if total <= budget:
        return result

    compacted = 0
    # Index 0 is the task the loop was given; compacting it would leave the
    # model guessing at its own objective.
    cutoff = max(len(result) - keep_recent, 0)
    for index in range(max(protect_head, 1), cutoff):
        if total <= budget:
            break
        replacement, saved = _compact_blocks(result[index])
        if not saved:
            continue
        result[index] = replacement
        total -= saved
        compacted += 1

    if compacted:
        logger.debug(
            "react_message_history_compacted messages=%d budget=%d est_tokens=%d",
            compacted,
            budget,
            total,
        )
    return result


__all__ = [
    "compact_history",
    "compact_message_history",
    "compact_messages",
    "history_token_budget",
    "message_token_size",
]
