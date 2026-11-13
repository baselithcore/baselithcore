"""Unit tests for the streaming output guard (holdback-window OutputGuard)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from baselith.core.orchestration.stream_guard import guard_stream


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


@pytest.mark.asyncio
@pytest.mark.parametrize("stride", [0, 1, 64, 500])
@pytest.mark.parametrize("size", [1, 5, 40])
async def test_no_stride_lets_pii_through(stride, size):
    """Filtering less often must not weaken redaction, whatever the chunking."""
    text = (
        "Write to alice.smith@example.com or bob@corp.example.org about the "
        "invoice; card 4111 1111 1111 1111 is on file. " * 6
    )
    chunks = [text[i : i + size] for i in range(0, len(text), size)]

    out = "".join([c async for c in guard_stream(_agen(chunks), stride=stride)])

    for secret in ("alice", "smith", "bob@", "corp", "4111"):
        assert secret not in out
    assert "[EMAIL_REDACTED]" in out and "[CREDIT_CARD_REDACTED]" in out


@pytest.mark.asyncio
async def test_a_larger_stride_leaves_no_more_residue_than_per_chunk():
    """Re-filtering a window that holds a placeholder leaves address fragments;
    filtering less often leaves fewer, never more."""
    text = "Write to bob@corp.example.org about the invoice. " * 8
    chunks = [text[i : i + 5] for i in range(0, len(text), 5)]

    per_chunk = "".join([c async for c in guard_stream(_agen(chunks), stride=0)])
    strided = "".join([c async for c in guard_stream(_agen(chunks), stride=64)])

    assert len(strided) <= len(per_chunk)


@pytest.mark.asyncio
async def test_stride_filters_less_often_than_per_chunk(monkeypatch):
    from baselith.core.orchestration import guard_pipeline

    _, output_guard = guard_pipeline._guards()
    calls = 0
    original = output_guard.filter

    def counting(text):
        nonlocal calls
        calls += 1
        return original(text)

    monkeypatch.setattr(output_guard, "filter", counting)
    chunks = ["x" * 5] * 200

    _ = [c async for c in guard_stream(_agen(chunks), stride=0)]
    per_chunk = calls
    calls = 0
    _ = [c async for c in guard_stream(_agen(chunks))]

    assert calls < per_chunk / 5
