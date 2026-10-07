"""Size-bounded reading of HTTP bodies and a streaming SSE parser.

Shared by the Streamable HTTP client transport and the OAuth client: both talk
to servers that are not ours, and neither may let one buffer an unbounded body.

Only uncompressed bodies are accepted. httpx inflates a whole network chunk
before handing it on, so a byte counter on decoded output runs *after* a gzip,
brotli or zstd bomb has already been expanded in memory: a few KiB on the wire
become gigabytes before any cap is consulted. Requests therefore ask for
``identity``, and a response that is encoded anyway is refused before its body
is read — so the only decoder httpx ever runs on it is the identity one.
"""

from __future__ import annotations

import codecs

import httpx

from core.mcp.client_errors import MCPTransportError


def too_large(limit: int) -> MCPTransportError:
    """The error for a body past the cap."""
    return MCPTransportError(f"MCP response too large (cap {limit} bytes)")


IDENTITY_ENCODING = "identity"
"""The only ``Accept-Encoding`` the client sends, and the only one it accepts."""


def check_response_headers(response: httpx.Response, limit: int) -> None:
    """Refuse up front a compressed body or a ``Content-Length`` over *limit*."""
    encoding = response.headers.get("content-encoding", "").strip().lower()
    if encoding not in ("", IDENTITY_ENCODING):
        raise MCPTransportError(
            f"MCP response uses Content-Encoding {encoding!r}; only an "
            "uncompressed body is accepted"
        )
    declared = response.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise too_large(limit)


async def read_capped(response: httpx.Response, limit: int) -> bytes:
    """Read a streamed response body, aborting once it passes *limit* bytes."""
    check_response_headers(response, limit)
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


__all__ = [
    "IDENTITY_ENCODING",
    "SSEParser",
    "check_response_headers",
    "read_capped",
    "too_large",
]
