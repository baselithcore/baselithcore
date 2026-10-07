"""Size-bounded reading of HTTP bodies and a streaming SSE parser.

Shared by the Streamable HTTP client transport and the OAuth client: both talk
to servers that are not ours, and neither may let one buffer an unbounded body.
"""

from __future__ import annotations

import codecs

import httpx

from core.mcp.client_errors import MCPTransportError


def too_large(limit: int) -> MCPTransportError:
    """The error for a body past the cap."""
    return MCPTransportError(f"MCP response too large (cap {limit} bytes)")


def check_declared_length(response: httpx.Response, limit: int) -> None:
    """Refuse up front when ``Content-Length`` already exceeds *limit*."""
    declared = response.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise too_large(limit)


async def read_capped(response: httpx.Response, limit: int) -> bytes:
    """Read a streamed response body, aborting once it passes *limit* bytes."""
    check_declared_length(response, limit)
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > limit:
            raise too_large(limit)
        chunks.append(chunk)
    return b"".join(chunks)


class SSEParser:
    """Incremental ``text/event-stream`` parser yielding each event's data."""

    def __init__(self) -> None:
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._buffer = ""

    def feed(self, chunk: bytes) -> list[str]:
        """Consume *chunk*; return the ``data`` payload of each completed event."""
        self._buffer = (self._buffer + self._decoder.decode(chunk)).replace(
            "\r\n", "\n"
        )
        *events, self._buffer = self._buffer.split("\n\n")
        payloads: list[str] = []
        for raw_event in events:
            data = [
                line[len("data:") :].lstrip(" ")
                for line in raw_event.split("\n")
                if line.startswith("data:")
            ]
            if data:
                payloads.append("\n".join(data))
        return payloads


__all__ = ["SSEParser", "check_declared_length", "read_capped", "too_large"]
