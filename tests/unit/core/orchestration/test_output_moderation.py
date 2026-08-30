"""Output-side content moderation — non-streaming and streaming.

Input moderation shipped first; the output side is opt-in
(``BASELITH_MODERATION_OUTPUT``, on top of the provider gate) because it adds
one moderation call per response. Non-streaming: the final response text is
replaced when flagged. Streaming: the accumulated text is checked at
intervals and the stream aborts before the flagging chunk is emitted —
already-delivered text cannot be recalled, which is inherent to streaming.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from core.guardrails.moderation import ModerationVerdict
from core.orchestration.guard_pipeline import guard_output_async
from core.orchestration.stream_guard import MODERATION_CHECK_INTERVAL, moderate_stream


class _StubModerator:
    def __init__(self, flagged: bool, raises: bool = False) -> None:
        self._flagged = flagged
        self._raises = raises
        self.calls = 0

    async def moderate(self, text: str) -> ModerationVerdict:
        self.calls += 1
        if self._raises:
            raise ConnectionError("moderation endpoint down")
        return ModerationVerdict(
            flagged=self._flagged, categories={"violence": 0.99}, provider="stub"
        )


@pytest.fixture()
def output_moderation_on(monkeypatch):
    monkeypatch.setenv("BASELITH_MODERATION_OUTPUT", "true")


def _install(monkeypatch, stub: _StubModerator) -> None:
    from core.guardrails import moderation as moderation_module

    monkeypatch.setattr(moderation_module, "get_moderator", lambda: stub)


async def _agen(chunks: list[str]) -> AsyncIterator[str]:
    for chunk in chunks:
        yield chunk


@pytest.mark.asyncio
async def test_flagged_response_is_replaced(monkeypatch, output_moderation_on):
    stub = _StubModerator(flagged=True)
    _install(monkeypatch, stub)

    result = await guard_output_async({"response": "something awful"})

    assert result["response"] == "Response blocked by content moderation."
    assert result["guardrails"]["moderation"]["blocked"] is True
    assert stub.calls == 1


@pytest.mark.asyncio
async def test_clean_response_untouched(monkeypatch, output_moderation_on):
    stub = _StubModerator(flagged=False)
    _install(monkeypatch, stub)

    result = await guard_output_async({"response": "a perfectly fine answer"})

    assert result["response"] == "a perfectly fine answer"


@pytest.mark.asyncio
async def test_output_moderation_off_by_default(monkeypatch):
    monkeypatch.delenv("BASELITH_MODERATION_OUTPUT", raising=False)
    stub = _StubModerator(flagged=True)
    _install(monkeypatch, stub)

    result = await guard_output_async({"response": "anything"})

    assert result["response"] == "anything"
    assert stub.calls == 0


@pytest.mark.asyncio
async def test_output_moderation_fails_open(monkeypatch, output_moderation_on):
    stub = _StubModerator(flagged=True, raises=True)
    _install(monkeypatch, stub)

    result = await guard_output_async({"response": "an answer"})

    assert result["response"] == "an answer"


@pytest.mark.asyncio
async def test_regex_output_guard_still_applies(monkeypatch, output_moderation_on):
    stub = _StubModerator(flagged=False)
    _install(monkeypatch, stub)

    result = await guard_output_async({"response": "mail leak@example.com"})

    assert "leak@example.com" not in result["response"]


@pytest.mark.asyncio
async def test_stream_aborts_when_accumulated_text_flags(
    monkeypatch, output_moderation_on
):
    stub = _StubModerator(flagged=True)
    _install(monkeypatch, stub)

    # Enough text to cross the first moderation checkpoint mid-stream.
    big = "x" * MODERATION_CHECK_INTERVAL
    chunks = ["ok ", big, "NEVER-EMITTED", "ALSO-NEVER"]
    out = [c async for c in moderate_stream(_agen(chunks))]

    joined = "".join(out)
    assert "NEVER-EMITTED" not in joined
    assert "ALSO-NEVER" not in joined
    assert "blocked" in joined.lower()  # abort marker for the client


@pytest.mark.asyncio
async def test_stream_below_interval_spends_no_moderation_calls(
    monkeypatch, output_moderation_on
):
    stub = _StubModerator(flagged=True)
    _install(monkeypatch, stub)

    out = [c async for c in moderate_stream(_agen(["short ", "answer"]))]

    assert "".join(out) == "short answer"
    assert stub.calls == 0


@pytest.mark.asyncio
async def test_stream_passthrough_when_output_moderation_off(monkeypatch):
    monkeypatch.delenv("BASELITH_MODERATION_OUTPUT", raising=False)
    stub = _StubModerator(flagged=True)
    _install(monkeypatch, stub)

    big = "x" * (2 * MODERATION_CHECK_INTERVAL)
    out = [c async for c in moderate_stream(_agen([big, "tail"]))]

    assert "".join(out) == big + "tail"
    assert stub.calls == 0


@pytest.mark.asyncio
async def test_stream_moderation_respects_master_kill_switch(
    monkeypatch, output_moderation_on
):
    # BASELITH_ORCHESTRATOR_GUARDRAILS must bypass the WHOLE guard pipeline,
    # streaming moderation included — same contract as guard_output_async.
    monkeypatch.setenv("BASELITH_ORCHESTRATOR_GUARDRAILS", "off")
    stub = _StubModerator(flagged=True)
    _install(monkeypatch, stub)

    big = "x" * (2 * MODERATION_CHECK_INTERVAL)
    out = [c async for c in moderate_stream(_agen([big, "tail"]))]

    assert "".join(out) == big + "tail"
    assert stub.calls == 0


@pytest.mark.asyncio
async def test_stream_moderation_fails_open(monkeypatch, output_moderation_on):
    stub = _StubModerator(flagged=True, raises=True)
    _install(monkeypatch, stub)

    big = "x" * (2 * MODERATION_CHECK_INTERVAL)
    out = [c async for c in moderate_stream(_agen([big, "tail"]))]

    assert "".join(out) == big + "tail"
