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
    # Words, not one unbroken run: a run that reaches the live edge is held
    # out of the filter pass altogether, which would leave nothing to count.
    chunks = ["word "] * 200

    _ = [c async for c in guard_stream(_agen(chunks), stride=0)]
    per_chunk = calls
    calls = 0
    _ = [c async for c in guard_stream(_agen(chunks))]

    assert calls < per_chunk / 5


# --- secrets longer than the holdback window ---------------------------------

_JWT = "eyJ" + "a" * 150 + "." + "b" * 180 + "." + "c" * 100
# Assembled at runtime so no literal key block sits in the repository for a
# secret scanner to flag; the content is filler, not a key.
_DASHES = "-" * 5
_PEM = (
    f"{_DASHES}BEGIN RSA PRIVATE KEY{_DASHES}\n"
    + "\n".join("MIIEvQIBADANBgkqhkiG9w0BAQEFAASC" * 2 for _ in range(26))
    + f"\n{_DASHES}END RSA PRIVATE KEY{_DASHES}"
)


def _split(text: str, size: int) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [1, 3, 7, 64, 200, 5000])
async def test_a_jwt_longer_than_the_window_is_redacted(size):
    text = f"the token is {_JWT} keep it safe. " + "tail " * 60
    out = "".join([c async for c in guard_stream(_agen(_split(text, size)))])
    assert "a" * 40 not in out and "b" * 40 not in out
    assert out.startswith("the token is ") and out.endswith("tail ")


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [1, 5, 31, 64, 200, 5000])
async def test_a_private_key_block_is_withheld_whole(size):
    text = f"here is the key:\n{_PEM}\nand the rest of the answer. " + "tail " * 60
    out = "".join([c async for c in guard_stream(_agen(_split(text, size)))])
    assert "MIIEvQIBADANBgkqhkiG9w0BAQEFAASC" not in out
    assert "PRIVATE KEY-----" not in out
    assert out.startswith("here is the key:\n")
    assert "and the rest of the answer." in out and out.endswith("tail ")


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [1, 50, 5000])
async def test_streaming_matches_the_non_streaming_filter_for_long_secrets(size):
    from baselith.core.orchestration import guard_pipeline

    _, output_guard = guard_pipeline._guards()
    text = f"a {_JWT} b\n{_PEM}\nc " + "tail " * 60
    out = "".join([c async for c in guard_stream(_agen(_split(text, size)))])
    assert out == output_guard.filter(text).filtered_output


@pytest.mark.asyncio
async def test_a_key_block_cut_off_by_the_end_of_the_stream_stays_withheld():
    text = "key:\n" + _PEM[: len(_PEM) // 2]
    out = "".join([c async for c in guard_stream(_agen(_split(text, 40)))])
    assert "MIIEvQIBADANBgkqhkiG9w0BAQEFAASC" not in out
    assert out.startswith("key:\n")


@pytest.mark.asyncio
async def test_an_unbroken_run_is_not_buffered_without_bound():
    from baselith.core.orchestration.stream_guard import MAX_TOKEN_HOLD

    blob = "Q" * (MAX_TOKEN_HOLD * 3)
    largest_gap = 0
    seen = 0
    fed = 0

    async def feed():
        nonlocal fed
        for piece in _split(blob, 256):
            fed += len(piece)
            yield piece

    async for piece in guard_stream(feed()):
        seen += len(piece)
        largest_gap = max(largest_gap, fed - seen)
    assert seen == len(blob)
    assert largest_gap <= MAX_TOKEN_HOLD + 1024


@pytest.mark.asyncio
async def test_text_without_word_spaces_keeps_streaming():
    """A non-ASCII script must not be held back as one long 'token'."""
    text = "これは長い日本語の文章です。" * 200
    pieces = [c async for c in guard_stream(_agen(_split(text, 20)))]
    assert "".join(pieces) == text
    assert len(pieces) > 20
