"""Newline-delimited JSON-RPC framing for the MCP stdio client transport.

The stdio transport is a single duplex byte stream: a server may interleave
notifications (``notifications/message``, ``notifications/progress``) with the
replies to in-flight requests, and a late reply to an abandoned request can
still be sitting in the pipe. Reading "the next line" and treating it as the
answer therefore corrupts results silently, so :func:`read_response`
demultiplexes on the JSON-RPC ``id`` instead.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any, Protocol

from core.config import get_mcp_config
from core.observability.logging import get_logger

logger = get_logger(__name__)


class _Reader(Protocol):
    """The slice of :class:`asyncio.StreamReader` this module needs."""

    async def readline(self) -> bytes: ...


class _Writer(Protocol):
    """The slice of :class:`asyncio.StreamWriter` this module needs."""

    def write(self, data: bytes, /) -> None: ...

    async def drain(self) -> None: ...


def validate_command(cmd: list[str]) -> None:
    """Reject a command whose executable is not allowlisted.

    Compares the basename of ``cmd[0]`` (case-insensitive, ``.exe`` stripped,
    version suffixes like ``python3.12`` normalized) against
    ``MCPConfig.allowed_command_basenames``. The current interpreter
    (``sys.executable``) is always permitted.

    Raises:
        ValueError: When the command is empty or not allowlisted.
    """
    if not cmd or not cmd[0]:
        raise ValueError("MCP command must not be empty")
    executable = cmd[0]
    if executable == sys.executable:
        return
    basename = os.path.basename(executable).lower()
    if basename.endswith(".exe"):
        basename = basename[: -len(".exe")]
    allowed = get_mcp_config().allowed_command_basenames
    # Accept versioned interpreter names (python3.12, node22) by also
    # checking the alphabetic prefix.
    prefix = basename.rstrip("0123456789.")
    if basename not in allowed and prefix not in allowed:
        raise ValueError(
            f"MCP command '{executable}' is not in the allowed command "
            f"list ({sorted(allowed)}). Set MCP_ALLOWED_COMMANDS to "
            "extend the allowlist if this binary is trusted."
        )


def resolve_command(server_script: str | None, command: list[str] | None) -> list[str]:
    """Pick the argv for a stdio server, enforcing the executable allowlist.

    An explicit *command* can come from a plugin manifest or operator config,
    so it is allowlisted before use; a *server_script* only ever resolves to
    the current interpreter or ``node``.

    Raises:
        ValueError: Neither input given, unknown script extension, or the
            explicit command is not allowlisted.
    """
    if command:
        validate_command(command)
        return command
    if not server_script:
        raise ValueError("No server script or command provided")
    if server_script.endswith(".py"):
        return [sys.executable, server_script]
    if server_script.endswith(".js"):
        return ["node", server_script]
    raise ValueError(
        "Server script must be .py or .js file (or provide a custom command)"
    )


async def spawn(
    cmd: list[str], env: dict[str, str] | None = None
) -> asyncio.subprocess.Process:
    """Start a stdio MCP server process with piped stdin/stdout.

    stderr is inherited so the server's logs surface in the parent's stderr,
    which the spec designates as the stdio server's logging channel.
    """
    process_env = os.environ.copy()
    if env:
        process_env.update(env)
    process_env["PYTHONUNBUFFERED"] = "1"

    return await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=sys.stderr,
        env=process_env,
    )


async def write_message(writer: _Writer, message: dict[str, Any]) -> None:
    """Frame *message* as one newline-terminated JSON line and flush it."""
    writer.write((json.dumps(message) + "\n").encode())
    await writer.drain()


async def read_response(
    reader: _Reader,
    request_id: Any,
    timeout: float,
    on_notification: Any | None = None,
) -> dict[str, Any]:
    """Read frames until the response carrying *request_id* arrives.

    Frames that are not that response — notifications (no ``id``) and replies
    to other request ids — are dropped, so they can never be mistaken for the
    result of the current call.

    Args:
        reader: Stream fed by the server's stdout.
        request_id: JSON-RPC id of the request awaiting a reply.
        timeout: Upper bound, in seconds, on the *total* wait across skipped
            frames — a chatty server cannot extend the deadline indefinitely.
        on_notification: Called with each server notification seen while
            waiting, so a caller can act on it (cache invalidation, progress)
            instead of losing it.

    Returns:
        The matching JSON-RPC response object.

    Raises:
        TimeoutError: The deadline elapsed before the reply arrived.
        RuntimeError: The server closed the connection.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout

    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise TimeoutError
        line = await asyncio.wait_for(reader.readline(), timeout=remaining)
        if not line:
            raise RuntimeError("Server closed connection")

        try:
            message = json.loads(line.decode().strip())
        except json.JSONDecodeError:
            # stdio servers must keep stdout clean; tolerate stray output
            # rather than failing the call on it.
            logger.warning(
                "mcp_stdio_invalid_frame", frame=line[:200].decode(errors="replace")
            )
            continue

        if not isinstance(message, dict):
            continue
        if "id" not in message:
            # Server-initiated notification: not our reply, but still news.
            logger.debug("mcp_stdio_notification", method=message.get("method"))
            if on_notification is not None:
                on_notification(message)
            continue
        if message["id"] != request_id:
            logger.debug("mcp_stdio_stale_response", received_id=message["id"])
            continue
        return message


__all__ = [
    "read_response",
    "resolve_command",
    "spawn",
    "validate_command",
    "write_message",
]
