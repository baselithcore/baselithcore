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

from core.observability.logging import get_logger

logger = get_logger(__name__)

#: Emit lag in characters. Must exceed the longest redaction pattern the
#: OutputGuard can match (credit cards ~19, SSN 11, typical emails < 64).
DEFAULT_HOLDBACK = 128

#: Chars of newly accumulated text between two output-moderation calls on a
#: stream. Bounds moderation-API spend on long answers; a short answer that
#: never crosses the interval spends zero calls mid-stream.
MODERATION_CHECK_INTERVAL = 512

#: Marker appended when a stream is cut by output moderation.
_MODERATION_ABORT_MARKER = "\n[Response blocked by content moderation]"


def _output_moderation_active() -> bool:
    """Whether the opt-in streaming/output moderation layer is on.

    Honors the same master kill switch as the rest of the guard pipeline
    (``BASELITH_ORCHESTRATOR_GUARDRAILS``): disabling the pipeline bypasses
    streaming moderation exactly like the non-streaming output guard.
    """
    import os

    from core.orchestration.guard_pipeline import _enabled

    if not _enabled():
        return False
    if os.environ.get("BASELITH_MODERATION_OUTPUT", "").strip().lower() not in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return False
    from core.guardrails import moderation

    return moderation.get_guardrails_config().moderation_enabled


async def moderate_stream(
    chunks: AsyncIterator[str],
    interval: int = MODERATION_CHECK_INTERVAL,
) -> AsyncIterator[str]:
    """Yield ``chunks``, aborting when accumulated output fails moderation.

    The accumulated text is moderated every ``interval`` newly buffered
    characters, BEFORE the chunk that crossed the boundary is emitted — a
    flagged stream stops with an abort marker and the flagging chunk (and
    everything after it) is never delivered. Text already emitted cannot be
    recalled; that is inherent to streaming. Moderator failures are
    fail-open, and the layer is a no-op unless ``BASELITH_MODERATION_OUTPUT``
    and a moderation provider are configured.
    """
    if not _output_moderation_active():
        async for chunk in chunks:
            yield chunk
        return

    from core.guardrails import moderation

    moderator = moderation.get_moderator()
    if moderator is None:
        async for chunk in chunks:
            yield chunk
        return

    accumulated = ""
    last_checked = 0
    async for chunk in chunks:
        accumulated += chunk
        if len(accumulated) - last_checked >= interval:
            last_checked = len(accumulated)
            try:
                verdict = await moderator.moderate(accumulated)
            except Exception as exc:
                logger.warning(
                    "stream_moderation_unavailable_fail_open",
                    extra={"error": str(exc)},
                )
                verdict = None
            if verdict is not None and verdict.flagged:
                logger.warning(
                    "stream_blocked_by_moderation",
                    extra={
                        "provider": verdict.provider,
                        "categories": sorted(verdict.categories),
                    },
                )
                yield _MODERATION_ABORT_MARKER
                return
        yield chunk


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


__all__ = [
    "DEFAULT_HOLDBACK",
    "MODERATION_CHECK_INTERVAL",
    "guard_stream",
    "moderate_stream",
]
