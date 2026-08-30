"""Streaming output guard: OutputGuard applied on the wire.

The non-streaming path filters the final response in one pass
(:func:`core.orchestration.guard_pipeline.guard_output`). A streamed response
leaves before it is complete, so filtering must happen chunk by chunk without
missing patterns split across chunk boundaries — the exact case a naive
per-chunk filter cannot catch.

This module applies the same :class:`~core.guardrails.output_guard.OutputGuard`
with a **holdback window**: text is emitted only once it is at least
``holdback`` characters behind the live edge, so any pattern shorter than the
window is fully buffered before its text is released. The retained tail is
already-filtered text; re-filtering it together with the next chunk is
idempotent (redaction placeholders never re-match their own pattern), so no
span is redacted twice or emitted unredacted.

Trade-offs, by design:

* Time-to-first-byte grows by one holdback window (default 128 chars).
* A single PII token longer than the window can straddle the emit boundary;
  the window covers every built-in pattern with ample margin.

Honors the same kill switch as the rest of the pipeline
(``BASELITH_ORCHESTRATOR_GUARDRAILS``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

#: Emit lag in characters. Must exceed the longest redaction pattern the
#: OutputGuard can match (credit cards ~19, SSN 11, typical emails < 64).
DEFAULT_HOLDBACK = 128


async def guard_stream(
    chunks: AsyncIterator[str],
    holdback: int = DEFAULT_HOLDBACK,
) -> AsyncIterator[str]:
    """Yield ``chunks`` with OutputGuard filtering applied across boundaries.

    Args:
        chunks: The raw response chunk stream from a stream handler.
        holdback: Emit lag in characters; text closer than this to the live
            edge stays buffered until more input (or the end) arrives.

    Yields:
        Filtered response chunks. Chunk boundaries may shift relative to the
        input; the concatenated output equals the OutputGuard-filtered text.
    """
    from core.orchestration.guard_pipeline import _enabled, _guards

    if not _enabled():
        async for chunk in chunks:
            yield chunk
        return

    _, output_guard = _guards()
    max_length = output_guard.config.max_output_length
    emitted = 0
    pending = ""

    def _cap(piece: str) -> str:
        """Trim ``piece`` to the cumulative output-length cap."""
        nonlocal emitted
        if emitted + len(piece) > max_length:
            piece = piece[: max_length - emitted]
        emitted += len(piece)
        return piece

    async for chunk in chunks:
        if not chunk:
            continue
        pending += chunk
        if len(pending) <= holdback:
            continue
        filtered = output_guard.filter(pending).filtered_output
        if len(filtered) > holdback:
            piece, pending = filtered[:-holdback], filtered[-holdback:]
            piece = _cap(piece)
            if piece:
                yield piece
            if emitted >= max_length:
                return
        else:
            # Redaction shrank the buffer back under the window; keep the
            # filtered text as the new tail and wait for more input.
            pending = filtered

    if pending:
        piece = _cap(output_guard.filter(pending).filtered_output)
        if piece:
            yield piece


__all__ = ["DEFAULT_HOLDBACK", "guard_stream"]
