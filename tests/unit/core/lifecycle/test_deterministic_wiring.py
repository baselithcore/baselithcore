"""``CORE_DETERMINISTIC_MODE`` must pin sampling on every LLM generation path.

The override kwargs were merged only into ``_generate_with_retry`` (plain
text). The agent loop runs on the message API, tool calling on the native
structured path, and chat streaming on ``generate_response_stream`` — none of
them saw the pin, so "deterministic" runs were not.
"""

from __future__ import annotations

import contextlib
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from core.config import get_core_config
from core.lifecycle.deterministic import (
    apply_deterministic_mode,
    get_llm_override_kwargs,
)
from core.services.llm.message_runtime import generate_messages
from core.services.llm.messages import Message
from core.services.llm.service import LLMService
from core.services.llm.stream_events import generate_stream_events
from core.services.llm.structured import generate_structured
from core.services.llm.tool_calling import LLMResult

MODEL = "gpt-4o-mini"
PIN = {"temperature": 0.0, "seed": 7, "top_p": 1.0}


@pytest.fixture
def deterministic():
    config = get_core_config()
    original = (config.deterministic_mode, config.random_seed)
    config.deterministic_mode, config.random_seed = True, 7
    try:
        yield
    finally:
        config.deterministic_mode, config.random_seed = original


@pytest.fixture(autouse=True)
def _no_tenant_gate(monkeypatch):
    async def _allow(model=None):
        return None

    monkeypatch.setattr(
        "core.quotas.cost_enforcement.enforce_tenant_cost_budget", _allow
    )


def _service(*, native: bool = False, messages: bool = False) -> LLMService:
    with patch("core.services.llm.service.get_llm_config") as config:
        config.return_value = Mock(
            provider="openai",
            model=MODEL,
            enable_cache=False,
            fallback_chain="",
            max_concurrent_requests=0,
            enable_native_tools=native or messages,
            thinking_enabled=False,
        )
        service = LLMService()
    service.provider = SimpleNamespace(
        supports_native_tools=native or messages,
        supports_messages=messages,
    )
    service.cost_tracker = None
    return service


def _assert_pinned(kwargs: dict) -> None:
    for key, value in PIN.items():
        assert kwargs[key] == value, key


async def test_message_path_is_pinned(deterministic):
    service = _service(messages=True)
    runner = AsyncMock(
        return_value=(LLMResult(text="hi", tokens_used=1), "openai", MODEL)
    )
    with patch(
        "core.services.llm.fallback_runtime.maybe_run_messages_with_fallback", runner
    ):
        await generate_messages(service, [Message.user("q")], model=MODEL)

    _assert_pinned(runner.await_args.kwargs)


async def test_native_structured_path_is_pinned(deterministic):
    service = _service(native=True)
    runner = AsyncMock(
        return_value=(LLMResult(text="hi", tokens_used=1), "openai", MODEL)
    )
    with patch(
        "core.services.llm.fallback_runtime.maybe_run_structured_with_fallback",
        runner,
    ):
        await generate_structured(service, "q", model=MODEL)

    _assert_pinned(runner.await_args.kwargs)


async def test_text_stream_is_pinned(deterministic):
    service = _service()
    seen: dict = {}

    async def _chunks():
        yield "hi", 5

    async def _open(svc, prompt, model, stream_kwargs):
        seen.update(stream_kwargs)
        return _chunks(), None, "openai", model

    with patch("core.services.llm._streaming.open_stream", _open):
        _ = [c async for c in service.generate_response_stream("q")]

    _assert_pinned(seen)


async def test_event_stream_is_pinned(deterministic):
    service = _service(native=True)
    seen: dict = {}

    async def _stream(prompt, model, **kwargs):
        seen.update(kwargs)
        return
        yield  # pragma: no cover - makes this an async generator

    service.provider.generate_structured_stream = _stream  # type: ignore[attr-defined]
    # An empty stream may fail post-processing; only the kwargs matter here.
    with contextlib.suppress(Exception):
        _ = [e async for e in generate_stream_events(service, "q", model=MODEL)]

    _assert_pinned(seen)


async def test_nothing_pinned_when_mode_off():
    service = _service(messages=True)
    runner = AsyncMock(
        return_value=(LLMResult(text="hi", tokens_used=1), "openai", MODEL)
    )
    with patch(
        "core.services.llm.fallback_runtime.maybe_run_messages_with_fallback", runner
    ):
        await generate_messages(service, [Message.user("q")], model=MODEL)

    assert "seed" not in runner.await_args.kwargs


def test_apply_seeds_random_but_not_the_hash_seed(deterministic, monkeypatch):
    monkeypatch.delenv("PYTHONHASHSEED", raising=False)
    with patch("random.seed") as seed:
        apply_deterministic_mode()
    seed.assert_called_once_with(7)
    # Writing it at runtime was a no-op for this interpreter; it is no
    # longer pretended.
    assert "PYTHONHASHSEED" not in os.environ


def test_anthropic_gets_temperature_only(deterministic):
    assert get_llm_override_kwargs("anthropic") == {"temperature": 0.0}
