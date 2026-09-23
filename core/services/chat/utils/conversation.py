"""Conversation memory on the live chat path.

``ChatRequest.conversation_id`` used to reach the orchestrator and stop there:
no handler read it and nothing wrote a turn, so every request was the first of
its conversation. These helpers load the prior turns before orchestration and
record the new one after it, for both the blocking and the streaming route.

The storage key binds the client-supplied ``conversation_id`` to the
authenticated user. The history cache is already tenant-scoped
(``core.chat.dependencies``), but inside one tenant an id chosen by the client
— ``"1"``, a replayed id — would otherwise hand one user's conversation to
another as prompt context.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from core.context import get_current_user_id
from core.observability.logging import get_logger
from core.services.chat.utils.history import ChatHistoryManager, HistoryTurns

logger = get_logger(__name__)

HISTORY_TEXT_KEY = "history_text"
_ANONYMOUS = "anonymous"


def conversation_key(conversation_id: str | None) -> str | None:
    """Return the storage key for ``conversation_id``, scoped to the caller.

    Args:
        conversation_id: The client-supplied conversation id.

    Returns:
        ``"<user>:<conversation_id>"``, or ``None`` when there is no id — a
        request without one is stateless by design.
    """
    if not conversation_id:
        return None
    return f"{get_current_user_id() or _ANONYMOUS}:{conversation_id}"


async def load_history(
    manager: ChatHistoryManager | None, key: str | None
) -> tuple[HistoryTurns, str]:
    """Load prior turns, degrading to none rather than failing the request."""
    if manager is None or key is None:
        return [], ""
    try:
        return await manager.load(key)
    except Exception as exc:
        logger.warning("chat_history_load_failed", error=str(exc))
        return [], ""


async def record_turn(
    manager: ChatHistoryManager | None,
    key: str | None,
    turns: HistoryTurns,
    query: str,
    answer: str,
) -> None:
    """Append the finished turn; a storage failure never fails the answer."""
    if manager is None or key is None:
        return
    try:
        await manager.append_turn(key, turns, query, answer)
    except Exception as exc:
        logger.warning("chat_history_append_failed", error=str(exc))


async def recording_stream(
    source: AsyncIterator[str],
    manager: ChatHistoryManager | None,
    key: str | None,
    turns: HistoryTurns,
    query: str,
) -> AsyncIterator[str]:
    """Pass a response stream through, recording the turn once it completes.

    A stream the client abandons (or that raises) is not recorded: a partial
    answer stored as the assistant's turn would be replayed to the model as
    something it said.
    """
    chunks: list[str] = []
    async for chunk in source:
        if isinstance(chunk, str):
            chunks.append(chunk)
        yield chunk
    await record_turn(manager, key, turns, query, "".join(chunks))


def build_chat_memory() -> Any | None:
    """Build the orchestrator's long-term memory, as the lazy registry does.

    Gated by ``CHAT_LONG_TERM_MEMORY_ENABLED`` (default off): the loop recalls
    past interactions before answering and writes the exchange back after, at
    an embedding and store round-trip per request. It gets the persistent
    provider and the summarizer ``core.bootstrap.lazy_init.initialize_memory``
    uses, so memories outlive the process and compaction can run. A
    construction failure degrades to no memory rather than failing chat.
    """
    try:
        from core.memory.manager import AgentMemory
        from core.memory.providers import build_memory_provider
        from core.services.llm.service import get_llm_service

        return AgentMemory(
            provider=build_memory_provider("agent_memory"),
            llm_service=get_llm_service(),
        )
    except Exception as exc:
        logger.warning("chat_memory_unavailable", error=str(exc))
        return None


def is_recordable(result: dict[str, Any]) -> bool:
    """Whether an orchestrator result is a real answer worth remembering."""
    return not result.get("error") and bool(result.get("response"))


__all__ = [
    "HISTORY_TEXT_KEY",
    "build_chat_memory",
    "conversation_key",
    "is_recordable",
    "load_history",
    "record_turn",
    "recording_stream",
]
