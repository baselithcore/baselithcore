"""Unit tests for the streaming output guard (holdback-window OutputGuard)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from core.orchestration.stream_guard import guard_stream


async def _agen(chunks: list[str]) -> AsyncIterator[str]:
    for chunk in chunks:
        yield chunk


@pytest.mark.asyncio
async def test_safe_text_passes_through_unchanged():
    chunks = ["Hello ", "world, ", "all good here."]
    out = [c async for c in guard_stream(_agen(chunks))]
    assert "".join(out) == "Hello world, all good here."


@pytest.mark.asyncio
async def test_redacts_pii_within_single_chunk():
    out = [c async for c in guard_stream(_agen(["mail me at leak@example.com thanks"]))]
    joined = "".join(out)
    assert "leak@example.com" not in joined
    assert "EMAIL" in joined


@pytest.mark.asyncio
async def test_redacts_pii_split_across_chunk_boundary():
    # The email is split mid-domain across two chunks: a naive per-chunk
    # filter cannot match it; the holdback window must.
    out = [c async for c in guard_stream(_agen(["contact leak@exam", "ple.com now"]))]
    joined = "".join(out)
    assert "leak@example.com" not in joined


@pytest.mark.asyncio
async def test_long_stream_emits_incrementally():
    # More text than one holdback window: the guard must not buffer the
    # entire stream and emit a single terminal chunk.
    chunks = ["safe text block. " for _ in range(64)]
    seen: list[str] = []
    emitted_before_end = False
    agen = guard_stream(_agen(chunks))
    async for piece in agen:
        seen.append(piece)
        if len(seen) > 1:
            emitted_before_end = True
    assert "".join(seen) == "".join(chunks)
    assert emitted_before_end


@pytest.mark.asyncio
async def test_kill_switch_disables_stream_guard(monkeypatch):
    monkeypatch.setenv("BASELITH_ORCHESTRATOR_GUARDRAILS", "off")
    out = [c async for c in guard_stream(_agen(["reach me: a@b.com"]))]
    assert "".join(out) == "reach me: a@b.com"
