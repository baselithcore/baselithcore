"""Conversation memory on the live chat path.

Regression: ``conversation_id`` reached the orchestrator and stopped there —
no handler read it and nothing recorded a turn — so every request was the
first of its conversation, while the docs called history "always-on".
"""

import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

sys.modules.setdefault("sentence_transformers", MagicMock())
sys.modules.setdefault("torch", MagicMock())

import pytest  # noqa: E402

from core.context import reset_user_context, set_user_context  # noqa: E402
from core.models.chat import ChatRequest  # noqa: E402
from core.orchestration.handlers.rag import build_rag_user_prompt  # noqa: E402
from core.services.chat.service import ChatService, ChatServiceConfig  # noqa: E402
from core.services.chat.utils.conversation import (  # noqa: E402
    conversation_key,
    recording_stream,
)
from core.services.chat.utils.history import ChatHistoryManager  # noqa: E402


class _DictCache:
    def __init__(self) -> None:
        self.store: dict[str, Any] = {}

    async def get(self, key: str) -> Any:
        return self.store.get(key)

    async def set(self, key: str, value: Any) -> None:
        self.store[key] = value


def _service(cache: _DictCache) -> ChatService:
    return ChatService(
        config=ChatServiceConfig(history_enabled=True),
        plugin_registry=MagicMock(),
        history_manager=ChatHistoryManager(cache, max_turns=6),
    )


def _agent(*answers: dict[str, Any]) -> MagicMock:
    agent = MagicMock()
    agent.process = AsyncMock(side_effect=list(answers))
    return agent


@pytest.mark.asyncio
async def test_second_turn_sees_the_first() -> None:
    service = _service(_DictCache())
    agent = _agent({"response": "Paris."}, {"response": "About 2 million."})

    with patch.object(ChatService, "agent", new=agent):
        await service.handle_chat_async(
            ChatRequest(query="Capital of France?", conversation_id="c1")
        )
        await service.handle_chat_async(
            ChatRequest(query="Its population?", conversation_id="c1")
        )

    first_ctx = agent.process.await_args_list[0].args[1]
    second_ctx = agent.process.await_args_list[1].args[1]
    assert first_ctx["history_text"] == ""
    assert "User: Capital of France?\nAssistant: Paris." in second_ctx["history_text"]


@pytest.mark.asyncio
async def test_without_conversation_id_nothing_is_stored() -> None:
    cache = _DictCache()
    service = _service(cache)
    with patch.object(ChatService, "agent", new=_agent({"response": "hi"})):
        await service.handle_chat_async(ChatRequest(query="hello"))
    assert cache.store == {}


@pytest.mark.asyncio
async def test_error_results_are_not_remembered() -> None:
    cache = _DictCache()
    service = _service(cache)
    agent = _agent({"response": "An error occurred.", "error": True})
    with patch.object(ChatService, "agent", new=agent):
        await service.handle_chat_async(ChatRequest(query="q", conversation_id="c1"))
    assert cache.store == {}


@pytest.mark.asyncio
async def test_same_conversation_id_is_isolated_per_user() -> None:
    cache = _DictCache()
    service = _service(cache)
    agent = _agent({"response": "noted"}, {"response": "other"})

    with patch.object(ChatService, "agent", new=agent):
        token = set_user_context("alice")
        try:
            await service.handle_chat_async(
                ChatRequest(query="private note", conversation_id="1")
            )
        finally:
            reset_user_context(token)
        token = set_user_context("mallory")
        try:
            await service.handle_chat_async(
                ChatRequest(query="what did I say?", conversation_id="1")
            )
        finally:
            reset_user_context(token)

    assert agent.process.await_args_list[1].args[1]["history_text"] == ""


@pytest.mark.asyncio
async def test_stream_records_the_turn_once_complete() -> None:
    cache = _DictCache()
    service = _service(cache)
    agent = MagicMock()

    async def _tokens():
        yield "Hel"
        yield "lo"

    agent.process_stream.return_value = _tokens()
    with patch.object(ChatService, "agent", new=agent):
        stream = await service.handle_chat_stream_async(
            ChatRequest(query="greet", conversation_id="s1")
        )
        assert [c async for c in stream] == ["Hel", "lo"]

    turns, _ = await service.history_manager.load(conversation_key("s1"))
    assert turns == [{"query": "greet", "answer": "Hello"}]


@pytest.mark.asyncio
async def test_abandoned_stream_is_not_recorded() -> None:
    cache = _DictCache()
    manager = ChatHistoryManager(cache, max_turns=6)

    async def _tokens():
        yield "partial"
        yield "rest"

    stream = recording_stream(_tokens(), manager, "k", [], "q")
    assert await stream.__anext__() == "partial"
    await stream.aclose()
    assert cache.store == {}


def test_prompt_carries_history_only_when_present() -> None:
    assert build_rag_user_prompt("ctx", "q").startswith("Context:")
    prompt = build_rag_user_prompt("ctx", "q", "User: a\nAssistant: b")
    assert prompt.startswith("Conversation so far:\nUser: a\nAssistant: b")
    assert prompt.index("Conversation so far") < prompt.index("Context:")


def test_long_term_memory_has_its_own_setting(monkeypatch):
    # Regression: the long-term flag shared CHAT_MEMORY_ENABLED with history
    # (opposite defaults) and ChatServiceConfig had no attribute for it, so
    # it could never be switched on.
    from core.config.chat import ChatConfig

    monkeypatch.setenv("CHAT_MEMORY_ENABLED", "true")
    monkeypatch.delenv("CHAT_LONG_TERM_MEMORY_ENABLED", raising=False)
    assert ChatConfig().long_term_memory_enabled is False
    monkeypatch.setenv("CHAT_LONG_TERM_MEMORY_ENABLED", "true")
    assert ChatConfig().long_term_memory_enabled is True
    assert ChatServiceConfig(memory_enabled=True).memory_enabled is True
    assert ChatServiceConfig().memory_enabled is False
