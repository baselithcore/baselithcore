"""
Chat history manager.

Provides conversation history management with caching and summarization.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from core.cache.protocols import AnyCache as CacheProtocol

HistoryTurns = List[Dict[str, str]]
HistorySummary = str
SUMMARY_HEADER = "Conversation summary:"
SUMMARY_DIVIDER = "\n\n---\n\n"


class ChatHistoryManager:
    """
    Manages conversation history with optional summarization.

    Stores conversation turns in a cache and can summarize older turns
    to maintain context while limiting memory usage.
    """

    def __init__(
        self,
        cache: Optional[CacheProtocol],
        *,
        max_turns: int,
        summary_enabled: bool = False,
        summary_max_turns: int = 8,
        summary_max_chars: int = 800,
    ) -> None:
        """
        Initialize history manager.

        Args:
            cache: Optional cache for storing history.
            max_turns: Maximum recent turns to keep.
            summary_enabled: Whether to summarize old turns.
            summary_max_turns: Max turns in summary.
            summary_max_chars: Max chars in summary.
        """
        self._cache = cache
        self._max_turns = max_turns
        self._summary_enabled = summary_enabled
        self._summary_max_turns = max(0, summary_max_turns)
        self._summary_max_chars = max(120, summary_max_chars)

    async def load(self, conversation_id: Optional[str]) -> Tuple[HistoryTurns, str]:
        """
        Load conversation history.

        Args:
            conversation_id: Unique conversation identifier.

        Returns:
            Tuple of (turns, formatted_history_text).
        """
        if not conversation_id or self._cache is None:
            return [], ""

        existing_turns, summary = await self._load_payload(conversation_id)
        if not existing_turns and not summary:
            return [], ""

        trimmed = existing_turns[-self._max_turns :]
        sections: List[str] = []
        summary_clean = summary.strip()
        if summary_clean:
            sections.append(f"{SUMMARY_HEADER}\n{summary_clean}")
        if trimmed:
            history_lines = [
                f"User: {turn['query']}\nAssistant: {turn['answer']}"
                for turn in trimmed
            ]
            sections.append("\n\n".join(history_lines))

        history_text = SUMMARY_DIVIDER.join(part for part in sections if part).strip()
        return trimmed, history_text

    async def append_turn(
        self,
        conversation_id: Optional[str],
        history_turns: HistoryTurns,
        user_query: str,
        answer: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Append a new turn to the conversation history.

        Args:
            conversation_id: Unique conversation identifier.
            history_turns: Existing history turns (unused, kept for compat).
            user_query: User's query text.
            answer: Assistant's answer text.
            metadata: Optional extra metadata.
        """
        if not conversation_id or self._cache is None:
            return

        sanitized_query = (user_query or "").strip()
        sanitized_answer = (answer or "").strip()
        if not sanitized_query or not sanitized_answer:
            return

        existing_turns, existing_summary = await self._load_payload(conversation_id)
        combined_turns = list(existing_turns)

        turn_data: Dict[str, Any] = {
            "query": sanitized_query,
            "answer": sanitized_answer,
        }
        if metadata:
            turn_data.update(metadata)

        combined_turns.append(turn_data)

        new_summary = existing_summary.strip()
        if self._summary_enabled and len(combined_turns) > self._max_turns:
            overflow_count = len(combined_turns) - self._max_turns
            overflow_turns = combined_turns[:overflow_count]
            recent_turns = combined_turns[overflow_count:]
            new_summary = self._merge_summary(existing_summary, overflow_turns)
            combined_turns = recent_turns
        else:
            combined_turns = combined_turns[-self._max_turns :]

        payload: Dict[str, Any] = {"turns": combined_turns}
        if new_summary:
            payload["summary"] = new_summary
        await self._cache.set(conversation_id, payload)

    async def _load_payload(
        self, conversation_id: str
    ) -> Tuple[HistoryTurns, HistorySummary]:
        """
        Retrieve history data from the cache.

        Args:
            conversation_id: Unique session identifier.

        Returns:
            Tuple[HistoryTurns, HistorySummary]: List of turns and summary text.
        """
        if self._cache is None:
            return [], ""

        stored = await self._cache.get(conversation_id)
        summary = ""
        turns: HistoryTurns = []

        if isinstance(stored, dict):
            summary = str(stored.get("summary") or "").strip()
            turns = self._sanitize_turns(stored.get("turns"))
        elif isinstance(stored, list):
            turns = self._sanitize_turns(stored)

        return turns, summary

    def _sanitize_turns(self, raw: Any) -> HistoryTurns:
        """
        Validate and format raw history data from the cache.

        Args:
            raw: Untrusted data from the cache.

        Returns:
            HistoryTurns: List of valid conversation turns.
        """
        sanitized: HistoryTurns = []
        if not isinstance(raw, list):
            return sanitized
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            past_query = (entry.get("query") or "").strip()
            past_answer = (entry.get("answer") or "").strip()
            if not past_query or not past_answer:
                continue

            clean_entry: Dict[str, str] = {"query": past_query, "answer": past_answer}
            sanitized.append(clean_entry)
        return sanitized

    def _merge_summary(
        self, previous_summary: str, overflow_turns: HistoryTurns
    ) -> str:
        """
        Update the summary with new overflow turns.

        Args:
            previous_summary: The existing summarized text.
            overflow_turns: The turns being moved out of the recent window.

        Returns:
            str: The updated and potentially truncated summary.
        """
        if not overflow_turns:
            return previous_summary.strip()

        summary_lines: List[str] = [
            line.strip() for line in previous_summary.splitlines() if line.strip()
        ]

        for turn in overflow_turns:
            summary_lines.append(self._format_summary_line(turn))

        if summary_lines and self._summary_max_turns > 0:
            summary_lines = summary_lines[-self._summary_max_turns :]

        summary_text = "\n".join(summary_lines).strip()
        return self._truncate_summary(summary_text)

    def _format_summary_line(self, turn: Dict[str, str]) -> str:
        """
        Format a single turn into a summary-friendly string.

        Args:
            turn: The turn dictionary with query and answer.

        Returns:
            str: Formatted line.
        """
        user_part = turn.get("query", "").strip()
        answer_part = turn.get("answer", "").strip()
        line = f"User: {user_part} → Assistant: {answer_part}"
        if len(line) > 240:
            line = f"{line[:237].rstrip()}..."
        return line

    def _truncate_summary(self, text: str) -> str:
        """
        Enforce character limits on the accumulated summary.

        Args:
            text: The full summary text.

        Returns:
            str: Truncated text with ellipsis if needed.
        """
        if len(text) <= self._summary_max_chars:
            return text

        truncated = text[: self._summary_max_chars].rstrip()
        if len(truncated) == self._summary_max_chars:
            truncated = truncated[:-1].rstrip()
        return f"{truncated}..."


__all__ = [
    "ChatHistoryManager",
    "CacheProtocol",
    "HistoryTurns",
    "HistorySummary",
]
