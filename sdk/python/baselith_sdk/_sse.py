"""Server-Sent Events decoding for ``/chat/stream``.

Split out of ``client.py`` (which was pushing the 500-line file-size cap):
frames the wire format emitted by ``plugins/api_routers/chat.py`` — splits on
blank lines, reassembles multi-line ``data:`` fields, ignores ``: keepalive``
comment lines, stops at ``event: done`` and raises :class:`ChatStreamError` on
``event: error`` — shared by the sync and async ``chat_stream`` methods.
"""

from __future__ import annotations

from typing import AsyncIterator, Iterator

from .errors import BaselithError


class ChatStreamError(BaselithError):
    """Raised when ``/chat/stream`` frames an ``event: error`` mid-stream.

    The server has already committed to a ``200`` response by the time a
    provider read fails, so the only way to report it is in-band (see
    ``plugins/api_routers/chat.py::SSE_ERROR_EVENT``). ``message`` is
    deliberately generic — the server never puts provider detail on the wire.
    """

    def __init__(self, message: str = "stream failed") -> None:
        super().__init__(message)
        self.message = message


class _SSEEvent:
    """One decoded SSE event: an optional ``event:`` name and its ``data:`` payload."""

    __slots__ = ("data", "event")

    def __init__(self, event: str | None, data: str) -> None:
        self.event = event
        self.data = data


def _parse_sse_block(block: str) -> _SSEEvent | None:
    """Parse one blank-line-delimited SSE block into an event.

    Returns ``None`` for a block that carries no event at all — e.g. one made
    only of ``: keepalive``-style comment lines.
    """
    event: str | None = None
    data_lines: list[str] = []
    for line in block.split("\n"):
        if not line or line.startswith(":"):
            continue  # blank line inside the block, or a comment (keepalive)
        if line.startswith("event:"):
            event = line[len("event:") :].strip()
        elif line.startswith("data:"):
            value = line[len("data:") :]
            if value.startswith(" "):
                value = value[1:]
            data_lines.append(value)
    if event is None and not data_lines:
        return None
    return _SSEEvent(event=event, data="\n".join(data_lines))


class _SSEDecoder:
    """Incremental text -> SSE-event decoder shared by the sync/async streams.

    Buffers raw text across chunk boundaries (a ``data:`` line, or the blank
    line ending an event, can arrive split across two reads) and yields one
    :class:`_SSEEvent` per complete, blank-line-terminated block.
    """

    def __init__(self) -> None:
        self._buffer = ""
        # A chunk boundary can fall exactly between a "\r" and its "\n": if the
        # trailing "\r" of one feed() were normalised on the spot, a "\n"
        # arriving at the start of the *next* feed() would read as a second,
        # spurious line break instead of completing the same "\r\n". So a
        # trailing bare "\r" is held back (not normalised yet) until the next
        # feed() or flush() resolves it, one way or the other.
        self._pending_cr = False

    def feed(self, text: str) -> Iterator[_SSEEvent]:
        """Feed newly-received text, yielding every event it completes."""
        if self._pending_cr:
            text = "\r" + text
            self._pending_cr = False
        if text.endswith("\r"):
            text = text[:-1]
            self._pending_cr = True
        self._buffer += text.replace("\r\n", "\n").replace("\r", "\n")
        while "\n\n" in self._buffer:
            block, self._buffer = self._buffer.split("\n\n", 1)
            event = _parse_sse_block(block)
            if event is not None:
                yield event

    def flush(self) -> Iterator[_SSEEvent]:
        """Yield one final event from a trailing, unterminated buffer (if any)."""
        if self._pending_cr:
            self._buffer += "\n"
            self._pending_cr = False
        if self._buffer.strip():
            event = _parse_sse_block(self._buffer)
            self._buffer = ""
            if event is not None:
                yield event


class _StreamDone(Exception):
    """Internal signal only: the ``event: done`` frame was seen."""


def _handle_sse_event(event: _SSEEvent) -> str:
    """Turn one decoded event into the text chunk it yields, or raise.

    Raises :class:`_StreamDone` on the terminal ``event: done`` frame (caught
    by the callers below to end the generator cleanly) and
    :class:`ChatStreamError` on ``event: error``.
    """
    if event.event == "done":
        raise _StreamDone
    if event.event == "error":
        raise ChatStreamError(event.data or "stream failed")
    return event.data


def _iter_sse_chunks(raw_chunks: Iterator[str]) -> Iterator[str]:
    """Decode a raw ``/chat/stream`` text stream into plain text chunks.

    Frames the wire format emitted by ``plugins/api_routers/chat.py``: splits
    on blank lines, reassembles multi-line ``data:`` fields, ignores
    ``: keepalive`` comment lines, stops at ``event: done`` and raises
    :class:`ChatStreamError` on ``event: error``.
    """
    decoder = _SSEDecoder()
    try:
        for raw in raw_chunks:
            for event in decoder.feed(raw):
                yield _handle_sse_event(event)
        for event in decoder.flush():
            yield _handle_sse_event(event)
    except _StreamDone:
        return


async def _aiter_sse_chunks(raw_chunks: AsyncIterator[str]) -> AsyncIterator[str]:
    """Async counterpart of :func:`_iter_sse_chunks`."""
    decoder = _SSEDecoder()
    try:
        async for raw in raw_chunks:
            for event in decoder.feed(raw):
                yield _handle_sse_event(event)
        for event in decoder.flush():
            yield _handle_sse_event(event)
    except _StreamDone:
        return
