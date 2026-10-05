"""Server-Sent Events decoding for ``/chat/stream`` and ``/runs/{run_id}/events``.

Split out of ``client.py`` (which was pushing the 500-line file-size cap):
frames the wire format emitted by ``plugins/api_routers/chat.py`` — splits on
blank lines, reassembles multi-line ``data:`` fields, ignores ``: keepalive``
comment lines, stops at ``event: done`` and raises :class:`ChatStreamError` on
``event: error`` — shared by the sync and async ``chat_stream`` methods.
"""

from __future__ import annotations

import json
from typing import AsyncIterator, Iterator

from .errors import BaselithError
from .models import RunEvent


class ChatStreamError(BaselithError):
    """Raised when ``/chat/stream`` frames an ``event: error`` mid-stream.

    The server has already committed to a ``200`` response by the time a
    provider read fails, so the only way to report it is in-band (see
    ``plugins/api_routers/chat.py::sse_error_event``). ``message`` is
    deliberately generic — the server never puts provider detail on the wire.
    Current servers send a JSON payload, whose ``code`` and ``request_id``
    (quote it when reporting the failure) are exposed here; older servers sent
    the bare text ``stream failed``, which still parses (both are ``None``).
    """

    def __init__(
        self,
        message: str = "stream failed",
        *,
        code: str | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.request_id = request_id

    def __str__(self) -> str:
        rid = f" (request_id={self.request_id})" if self.request_id else ""
        return f"{self.message}{rid}"

    @classmethod
    def from_event_data(cls, data: str) -> ChatStreamError:
        """Build from an ``event: error`` payload: JSON object or legacy text."""
        try:
            payload = json.loads(data) if data.lstrip().startswith("{") else None
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            return cls(data or "stream failed")
        detail = payload.get("detail") or payload.get("message") or "stream failed"
        code = payload.get("code")
        rid = payload.get("request_id")
        return cls(
            str(detail),
            code=str(code) if code is not None else None,
            request_id=str(rid) if rid is not None else None,
        )


class _SSEEvent:
    """One decoded SSE event: optional ``event:`` name and ``id:``, plus ``data:``."""

    __slots__ = ("data", "event", "id")

    def __init__(self, event: str | None, data: str, id: str | None = None) -> None:
        self.event = event
        self.data = data
        self.id = id


def _parse_sse_block(block: str) -> _SSEEvent | None:
    """Parse one blank-line-delimited SSE block into an event.

    Returns ``None`` for a block that carries no event at all — e.g. one made
    only of ``: keepalive``-style comment lines.
    """
    event: str | None = None
    event_id: str | None = None
    data_lines: list[str] = []
    for line in block.split("\n"):
        if not line or line.startswith(":"):
            continue  # blank line inside the block, or a comment (keepalive)
        if line.startswith("event:"):
            event = line[len("event:") :].strip()
        elif line.startswith("id:"):
            event_id = line[len("id:") :].strip()
        elif line.startswith("data:"):
            value = line[len("data:") :]
            if value.startswith(" "):
                value = value[1:]
            data_lines.append(value)
    if event is None and not data_lines:
        return None
    return _SSEEvent(event=event, data="\n".join(data_lines), id=event_id)


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
        raise ChatStreamError.from_event_data(event.data)
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


def _to_run_event(event: _SSEEvent) -> RunEvent:
    """Build a :class:`RunEvent` from one ``/runs/{run_id}/events`` frame.

    The frame is ``id: <AgentEvent.id>`` + ``event: <type>`` + ``data:
    <AgentEvent JSON>``. Unlike ``/chat/stream``, ``event: error`` here is an
    ordinary (terminal) run event, not a transport failure, so it is yielded.
    """
    try:
        payload = json.loads(event.data) if event.data else {}
    except ValueError:
        payload = {"content": event.data}
    if not isinstance(payload, dict):
        payload = {"content": event.data}
    payload.setdefault("type", event.event or "message")
    if event.id is not None:
        payload["id"] = event.id
    return RunEvent.model_validate(payload)


def _iter_run_events(raw_chunks: Iterator[str]) -> Iterator[RunEvent]:
    """Decode a raw run-event SSE stream; stops after a terminal event."""
    decoder = _SSEDecoder()
    for raw in raw_chunks:
        for event in decoder.feed(raw):
            run_event = _to_run_event(event)
            yield run_event
            if run_event.is_terminal:
                return
    for event in decoder.flush():
        yield _to_run_event(event)


async def _aiter_run_events(raw_chunks: AsyncIterator[str]) -> AsyncIterator[RunEvent]:
    """Async counterpart of :func:`_iter_run_events`."""
    decoder = _SSEDecoder()
    async for raw in raw_chunks:
        for event in decoder.feed(raw):
            run_event = _to_run_event(event)
            yield run_event
            if run_event.is_terminal:
                return
    for event in decoder.flush():
        yield _to_run_event(event)
