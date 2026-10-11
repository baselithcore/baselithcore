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
import atexit
import json
import os
import shutil
import sys
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from baselith.core.config import get_mcp_config
from baselith.core.mcp.client_errors import MCPTransportError
from baselith.core.mcp.client_limits import client_limits
from baselith.core.mcp.reply_matching import (
    is_notification,
    is_reply,
    is_server_request,
)
from baselith.core.mcp.stdio_args import (
    check_hygiene,
    family_of,
    is_package_runner,
    plan_arguments,
)
from baselith.core.mcp.stdio_binaries import (
    allowed_names,
    is_denied,
    normalize_name,
    resolve_binary,
)
from baselith.core.mcp.stdio_env import build_child_env
from baselith.core.observability.logging import get_logger

logger = get_logger(__name__)


class _Reader(Protocol):
    """The slice of :class:`asyncio.StreamReader` this module needs."""

    async def readline(self) -> bytes: ...


class _Writer(Protocol):
    """The slice of :class:`asyncio.StreamWriter` this module needs."""

    def write(self, data: bytes, /) -> None: ...

    async def drain(self) -> None: ...


_TEMP_CWD: str | None = None


def _private_empty_cwd() -> str:
    """A 0700 empty directory, created once per process and removed at exit.

    A server that has no script directory of its own (``python -m``, a package
    runner) must still not inherit the parent's cwd: ``python -m`` puts the
    cwd on ``sys.path``, so a planted ``module.py`` there would win.
    """
    global _TEMP_CWD
    if _TEMP_CWD is None or not os.path.isdir(_TEMP_CWD):
        _TEMP_CWD = tempfile.mkdtemp(prefix="baselith-mcp-stdio-")
        atexit.register(shutil.rmtree, _TEMP_CWD, True)
    return _TEMP_CWD


@dataclass(frozen=True)
class StdioLaunch:
    """A fully vetted way to start a stdio server.

    Attributes:
        argv: Argument vector with an absolute ``argv[0]`` in a trusted directory.
        cwd: Working directory to spawn in (never the parent's).
    """

    argv: list[str]
    cwd: str


def plan_launch(
    cmd: list[str],
    allowed: frozenset[str] | None = None,
    allow_package_runners: bool | None = None,
) -> StdioLaunch:
    """Validate *cmd* completely and say exactly how it will be started.

    This is the single gate every stdio spawn passes through, and it runs at
    spawn time - not only when configuration is loaded - so a file or symlink
    changed since then is judged as it is now. In order: argv is bounded and
    clean; ``argv[0]`` is not a shell or exec-wrapper; its basename matches the
    allowlist *exactly*; it resolves inside a trusted bin directory (``PATH`` is
    never consulted) to a vetted file; the arguments are valid for the command
    family (no inline code, no remote code, no package install, a script inside
    ``MCP_STDIO_ALLOWED_DIRS``); package runners need
    ``MCP_ALLOW_PACKAGE_RUNNERS``. No shell is involved anywhere.

    Raises:
        ValueError: Naming the first rule the command breaks.
    """
    if not cmd or not cmd[0]:
        raise ValueError("MCP command must not be empty")
    check_hygiene(cmd)
    executable = cmd[0]
    if is_denied(executable):
        raise ValueError(
            f"MCP command '{executable}' is a shell or exec-wrapper and is never "
            "launchable, whatever MCP_ALLOWED_COMMANDS says"
        )
    config = get_mcp_config()
    names = set(allowed_names(allowed))
    if executable == sys.executable:
        names.add(normalize_name(os.path.basename(executable)))
    if normalize_name(os.path.basename(executable)) not in names:
        raise ValueError(
            f"MCP command '{executable}' is not in the allowed command list "
            f"({sorted(names)}). Set MCP_ALLOWED_COMMANDS to extend the "
            "allowlist if this binary is trusted."
        )
    if allow_package_runners is None:
        allow_package_runners = getattr(config, "mcp_allow_package_runners", False)
    if not allow_package_runners and is_package_runner(os.path.basename(executable)):
        # Say so before looking for the binary: the fix is a setting, not an install.
        raise ValueError(
            f"MCP command '{executable}' downloads and runs a package at spawn "
            "time; set MCP_ALLOW_PACKAGE_RUNNERS=true to permit package runners."
        )
    path, real = resolve_binary(executable)
    family = family_of(os.path.basename(path), os.path.basename(real))
    if executable == sys.executable and family is None:
        family = "python"
    plan = plan_arguments(family, cmd[1:], bool(allow_package_runners))
    cwd = os.path.dirname(plan.script) if plan.script else _private_empty_cwd()
    return StdioLaunch(argv=[path, *cmd[1:]], cwd=cwd)


def validate_command(
    cmd: list[str],
    allowed: frozenset[str] | None = None,
    allow_package_runners: bool | None = None,
) -> None:
    """Reject a command that is not a safe, allowlisted stdio launch.

    See :func:`plan_launch` for the rules.

    Args:
        cmd: Argv to validate.
        allowed: Explicit allowlist of executable names; defaults to the global
            MCP config's ``allowed_command_basenames``.
        allow_package_runners: Override for ``MCP_ALLOW_PACKAGE_RUNNERS``.

    Raises:
        ValueError: The command breaks a rule.
    """
    plan_launch(cmd, allowed, allow_package_runners)


def resolve_command(server_script: str | None, command: list[str] | None) -> list[str]:
    """Pick the argv for a stdio server, enforcing every :func:`plan_launch` rule.

    An explicit *command* can come from a plugin manifest or operator config; a
    *server_script* only ever runs through the current interpreter (``.py``) or
    ``node`` (``.js``), and its path is held to the same allowed-directory rule.

    Raises:
        ValueError: Neither input given, unknown script extension, or the
            command breaks a rule.
    """
    if command:
        return plan_launch(command).argv
    if not server_script:
        raise ValueError("No server script or command provided")
    if server_script.endswith(".py"):
        return plan_launch([sys.executable, server_script]).argv
    if server_script.endswith(".js"):
        return plan_launch(["node", server_script]).argv
    raise ValueError(
        "Server script must be .py or .js file (or provide a custom command)"
    )


async def spawn(
    cmd: list[str],
    env: dict[str, str] | None = None,
    *,
    max_line_bytes: int | None = None,
) -> asyncio.subprocess.Process:
    """Start a stdio MCP server process with piped stdin/stdout.

    *cmd* is validated again here (:func:`plan_launch`), so the exec sees what
    was checked a moment ago rather than what was configured earlier. The child
    gets :func:`build_child_env` - a sanitised environment plus *env* - and a
    fixed working directory, never the parent's. stderr is inherited so the
    server's logs surface in the parent's stderr, which the spec designates as
    the stdio server's logging channel. The stdout reader is created with a
    *max_line_bytes* buffer limit so a line that never ends cannot grow without
    bound.
    """
    launch = plan_launch(cmd)
    child_env = build_child_env(env)
    limit = max_line_bytes or client_limits().max_line_bytes
    return await asyncio.create_subprocess_exec(
        *launch.argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=sys.stderr,
        env=child_env,
        cwd=launch.cwd,
        limit=limit,
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
    *,
    writer: _Writer | None = None,
    on_server_request: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
    | None = None,
    max_line_bytes: int | None = None,
) -> dict[str, Any]:
    """Read frames until the reply to *request_id* arrives.

    Only a message with ``result`` or ``error``, no ``method`` and an id equal
    to ours is the reply (:func:`~baselith.core.mcp.reply_matching.is_reply`); a server
    *request* is never mistaken for it, even one reusing our id. Server
    requests are answered through *on_server_request* and the response written
    back on *writer*; notifications go to *on_notification*; replies to other
    ids are dropped.

    Args:
        reader: Stream fed by the server's stdout.
        request_id: JSON-RPC id of the request awaiting a reply.
        timeout: Upper bound, in seconds, on the *total* wait across skipped
            frames - a chatty or dribbling server cannot extend the deadline.
        on_notification: Called with each server notification seen while
            waiting.
        writer: Where responses to server requests are written.
        on_server_request: Builds the response to a server request.
        max_line_bytes: Cap on one frame; defaults to
            ``MCP_CLIENT_MAX_LINE_BYTES``.

    Returns:
        The matching JSON-RPC response object.

    Raises:
        TimeoutError: The deadline elapsed before the reply arrived.
        MCPTransportError: A frame exceeded the cap, or the server closed the
            connection.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    cap = max_line_bytes or client_limits().max_line_bytes

    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise TimeoutError
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=remaining)
        except (ValueError, asyncio.LimitOverrunError) as exc:
            raise MCPTransportError(
                f"MCP stdio frame too large (cap {cap} bytes)"
            ) from exc
        if not line:
            raise MCPTransportError("Server closed connection")
        if len(line) > cap:
            raise MCPTransportError(f"MCP stdio frame too large (cap {cap} bytes)")

        try:
            message = json.loads(line.decode().strip())
        except (json.JSONDecodeError, UnicodeDecodeError):
            # stdio servers must keep stdout clean; tolerate stray output
            # rather than failing the call on it.
            logger.warning("mcp_stdio_invalid_frame", frame_bytes=len(line))
            continue

        if not isinstance(message, dict):
            continue
        if is_reply(message, request_id):
            return message
        if is_server_request(message):
            logger.debug("mcp_stdio_server_request", method=message.get("method"))
            if on_server_request is not None and writer is not None:
                response = await asyncio.wait_for(
                    on_server_request(message),
                    timeout=max(deadline - loop.time(), 0.001),
                )
                await write_message(writer, response)
        elif is_notification(message):
            # Server-initiated notification: not our reply, but still news.
            logger.debug("mcp_stdio_notification", method=message.get("method"))
            if on_notification is not None:
                on_notification(message)
        else:
            logger.debug("mcp_stdio_stale_response", received_id=message.get("id"))


__all__ = [
    "StdioLaunch",
    "build_child_env",
    "plan_launch",
    "read_response",
    "resolve_command",
    "spawn",
    "validate_command",
    "write_message",
]
