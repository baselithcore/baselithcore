"""Streaming output guard: OutputGuard applied on the wire.

The non-streaming path filters the final response in one pass
(:func:`baselith.core.orchestration.guard_pipeline.guard_output`). A streamed response
leaves before it is complete, so filtering must happen chunk by chunk without
missing patterns split across chunk boundaries — the exact case a naive
per-chunk filter cannot catch.

This module applies the same :class:`~baselith.core.guardrails.output_guard.OutputGuard`
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

import re
from collections.abc import AsyncIterator

from baselith.core.observability.logging import get_logger

logger = get_logger(__name__)

#: Emit lag in characters. Must exceed the longest redaction pattern the
#: OutputGuard can match (credit cards ~19, SSN 11, typical emails < 64).
DEFAULT_HOLDBACK = 128

#: New characters that must accumulate past the holdback window before the
#: guard filters again. Filtering the ~130-character window on every chunk cost
#: ~19 µs a chunk (and an NER pass per chunk under the Presidio engine). Text
#: is emitted in slightly larger pieces. Nothing sensitive gets through that
#: did not before, and the output is cleaner: re-filtering a window that
#: already holds a placeholder left fragments of the redacted address behind.
DEFAULT_FILTER_STRIDE = 64

#: Longest unbroken credential-alphabet run the guard keeps whole before it
#: gives up and lets the text flow. The holdback window is sized for the short
#: patterns (cards, emails); a JWT or a prefixed API key is one run of several
#: hundred characters, and a cut that lands inside it emits the head of the
#: token before the pattern that would have redacted it can match.
MAX_TOKEN_HOLD = 8192

#: Characters a prefixed key, a JWT or a base64 body is made of. ASCII only on
#: purpose: a "non-whitespace run" would hold back whole paragraphs of text in
#: scripts that do not separate words with spaces.
_NOT_TOKEN_CHAR = re.compile(r"[^A-Za-z0-9_\-.+/=]")

_KEY_BEGIN_RE = re.compile(r"-----BEGIN[^-]*PRIVATE KEY-----", re.IGNORECASE)
_KEY_END_RE = re.compile(r"-----END[^-]*PRIVATE KEY-----", re.IGNORECASE)
#: Tail kept while a key block is open: enough for an END line split across
#: chunks, and nothing of the body beyond it.
_KEY_END_LOOKBACK = 64
#: An empty block the OutputGuard redacts with its own placeholder. Stands in
#: for a block whose body was withheld, so the reader sees the same marker the
#: non-streaming path produces.
_KEY_STUB = "-----BEGIN PRIVATE KEY-----\n-----END PRIVATE KEY-----"

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

    from baselith.core.orchestration.guard_pipeline import _enabled

    if not _enabled():
        return False
    if os.environ.get("BASELITH_MODERATION_OUTPUT", "").strip().lower() not in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return False
    from baselith.core.guardrails import moderation

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

    from baselith.core.guardrails import moderation

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


def _open_key_block(text: str) -> int | None:
    """Start of a private-key block in ``text`` whose END has not arrived yet."""
    if "-----" not in text:
        return None
    last = None
    for last in _KEY_BEGIN_RE.finditer(text):
        pass
    if last is None or _KEY_END_RE.search(text, last.end()) is not None:
        return None
    return last.start()


def _live_run_start(text: str) -> int:
    """Index where the token run touching the end of ``text`` begins.

    A run that reaches the live edge may still be growing: a pattern can match
    its prefix today (a JWT whose third segment has ten characters so far) and
    miss the rest tomorrow, or not match yet at all while its head scrolls out
    of the window. Such a run is kept out of the filter pass until something
    ends it.

    Returns:
        ``len(text)`` when the text does not end inside a run, or when the run
        is longer than :data:`MAX_TOKEN_HOLD` (the guard does not buffer
        without bound).
    """
    size = len(text)
    if not size or _NOT_TOKEN_CHAR.match(text, size - 1):
        return size
    # Search backwards: the first non-token character of the reversed tail is
    # the last one before the run.
    boundary = _NOT_TOKEN_CHAR.search(text[: -MAX_TOKEN_HOLD - 2 : -1])
    if boundary is None:
        return size if size > MAX_TOKEN_HOLD else 0
    return size - boundary.start()


async def guard_stream(
    chunks: AsyncIterator[str],
    holdback: int = DEFAULT_HOLDBACK,
    stride: int = DEFAULT_FILTER_STRIDE,
) -> AsyncIterator[str]:
    """Yield ``chunks`` with OutputGuard filtering applied across boundaries.

    Args:
        chunks: The raw response chunk stream from a stream handler.
        holdback: Emit lag in characters; text closer than this to the live
            edge stays buffered until more input (or the end) arrives.
        stride: Characters beyond the window to accumulate between filter
            passes (``0`` filters on every chunk).

    Yields:
        Filtered response chunks. Chunk boundaries may shift relative to the
        input; the concatenated output equals the OutputGuard-filtered text.
    """
    from baselith.core.orchestration.guard_pipeline import _enabled, _guards

    if not _enabled():
        async for chunk in chunks:
            yield chunk
        return

    _, output_guard = _guards()
    max_length = output_guard.config.max_output_length
    emitted = 0
    pending = ""
    # Private-key blocks are the one redaction that spans kilobytes. While one
    # is open, its body is withheld (not buffered) and only a short tail is
    # kept to recognise the END line. Skipped entirely when the operator has
    # turned that redaction off: the guard must not alter what it is not asked
    # to redact.
    guards_keys = output_guard.filter(_KEY_STUB).filtered_output != _KEY_STUB
    key_tail: str | None = None
    # Length of ``pending`` after the last filter pass. The next pass waits for
    # ``stride`` characters beyond it — not beyond the window — so a buffer the
    # guard is holding whole (a long token) is not re-filtered on every chunk.
    settled = holdback

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
        if key_tail is not None:
            key_tail += chunk
            closed = _KEY_END_RE.search(key_tail)
            if closed is None:
                key_tail = key_tail[-_KEY_END_LOOKBACK:]
                continue
            pending, key_tail = _KEY_STUB + key_tail[closed.end() :], None
        else:
            pending += chunk
        open_at = _open_key_block(pending) if guards_keys else None
        if open_at is not None:
            head = pending[:open_at]
            key_tail, pending = pending[open_at:][-_KEY_END_LOOKBACK:], ""
            piece = _cap(output_guard.filter(head).filtered_output) if head else ""
            if piece:
                yield piece
            if emitted >= max_length:
                return
            continue
        if len(pending) < max(settled, holdback) + stride:
            continue
        edge = _live_run_start(pending)
        live = pending[edge:]
        filtered = output_guard.filter(pending[:edge]).filtered_output if edge else ""
        # Emit what lies more than ``holdback`` behind the live edge — but
        # never any of the unfiltered run itself.
        cut = min(len(filtered), len(filtered) + len(live) - holdback)
        if cut > 0:
            piece = _cap(filtered[:cut])
            pending = filtered[cut:] + live
            settled = len(pending)
            if piece:
                yield piece
            if emitted >= max_length:
                return
        else:
            # Redaction shrank the buffer back under the window, or all of it
            # is one live run: keep it and wait for more input.
            pending = filtered + live
            settled = len(pending)

    if key_tail is not None:
        # The stream ended inside a key block: what was withheld stays withheld.
        pending = _KEY_STUB
    if pending:
        piece = _cap(output_guard.filter(pending).filtered_output)
        if piece:
            yield piece


__all__ = [
    "DEFAULT_FILTER_STRIDE",
    "DEFAULT_HOLDBACK",
    "MAX_TOKEN_HOLD",
    "MODERATION_CHECK_INTERVAL",
    "guard_stream",
    "moderate_stream",
]
