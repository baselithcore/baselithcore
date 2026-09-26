"""Shutdown helpers the lifespan calls before and after the pools close."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.api import _runtime_services
from core.chat import service as chat_service_module
from core.services.llm.providers._anthropic_request import forwardable_kwargs


async def test_drain_orchestrator_awaits_aclose() -> None:
    orchestrator = SimpleNamespace(aclose=AsyncMock())
    fake_service = SimpleNamespace(_agent=orchestrator)
    with patch.object(chat_service_module, "_chat_service", fake_service):
        await _runtime_services.drain_orchestrator()
    orchestrator.aclose.assert_awaited_once()


async def test_drain_orchestrator_is_a_noop_without_a_chat_service() -> None:
    with patch.object(chat_service_module, "_chat_service", None):
        await _runtime_services.drain_orchestrator()


async def test_drain_orchestrator_swallows_errors() -> None:
    orchestrator = SimpleNamespace(aclose=AsyncMock(side_effect=RuntimeError("x")))
    fake_service = SimpleNamespace(_agent=orchestrator)
    with patch.object(chat_service_module, "_chat_service", fake_service):
        await _runtime_services.drain_orchestrator()


async def test_close_shared_clients_closes_both_and_survives_failures() -> None:
    close_llm = AsyncMock(side_effect=RuntimeError("llm"))
    close_vs = AsyncMock()
    with (
        patch("core.services.llm.runtime.close_llm_services", close_llm),
        patch("core.services.vectorstore.service.close_vectorstore_service", close_vs),
    ):
        await _runtime_services.close_shared_clients()
    close_llm.assert_awaited_once()
    close_vs.assert_awaited_once()


def test_anthropic_never_receives_openai_seed() -> None:
    forwarded = forwardable_kwargs({"seed": 42, "stop_sequences": ["x"]})
    assert "seed" not in forwarded
    assert forwarded == {"stop_sequences": ["x"]}
