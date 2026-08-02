"""stdio transport for :class:`~core.mcp.server.MCPServer`.

Reads newline-delimited JSON-RPC from stdin and writes responses to stdout —
the transport Claude Desktop and most MCP-aware IDEs speak. Requests are handed
to a :class:`~core.mcp.dispatch.RequestDispatcher`, so a long-running tool
neither blocks the read loop nor escapes cancellation. Writes are serialized
behind a lock because responses and progress notifications now race for the
same stream.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from core.mcp.dispatch import RequestDispatcher
from core.observability.logging import get_logger

logger = get_logger(__name__)


async def _stdio_streams() -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Wrap the process's stdin/stdout as asyncio streams."""
    loop = asyncio.get_running_loop()

    reader = asyncio.StreamReader()
    await loop.connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader), sys.stdin
    )

    transport, protocol = await loop.connect_write_pipe(
        asyncio.streams.FlowControlMixin, sys.stdout
    )
    writer = asyncio.StreamWriter(transport, protocol, reader, loop)
    return reader, writer


async def serve_stdio(server: Any) -> None:
    """Serve *server* over stdio until stdin closes or the server stops."""
    logger.info("mcp_server_starting", transport="stdio", name=server.info.name)
    reader, writer = await _stdio_streams()
    write_lock = asyncio.Lock()

    async def send(message: dict[str, Any]) -> None:
        # One writer, many producers (responses + progress notifications):
        # without the lock two frames could interleave on the same line.
        async with write_lock:
            writer.write((json.dumps(message) + "\n").encode())
            await writer.drain()

    dispatcher = RequestDispatcher(server.handle_message, send)

    try:
        while server.is_running:
            line = await reader.readline()
            if not line:
                break

            try:
                message = json.loads(line.decode().strip())
            except json.JSONDecodeError as exc:
                logger.warning("mcp_invalid_json", error=str(exc))
                await send(server._error_response(None, -32700, "Parse error"))
                continue

            await dispatcher.dispatch(message)

        await dispatcher.drain()
    except asyncio.CancelledError:
        logger.info("mcp_server_cancelled")
        dispatcher.cancel_all()
        raise
    finally:
        server.stop()
        logger.info("mcp_server_stopped")


__all__ = ["serve_stdio"]
