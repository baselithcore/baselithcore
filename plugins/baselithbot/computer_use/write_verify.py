"""Post-write verification of Python files written through ``fs_write``.

After a successful write of a ``.py`` file the source is compiled with the
stdlib ``py_compile`` (cheap, offline, deterministic). A syntax error is
surfaced as a ``verification: compile failed: ...`` marker on the tool
result — the file is deliberately KEPT on disk so the agent can read the
error and fix it; the marker IS the feedback loop.

The behavior is gated by ``ComputerUseConfig.post_write_verify``, whose
default comes from the ``BASELITH_POST_WRITE_VERIFY`` env flag (ON unless
set to ``0``/``false``/``no``/``off``).

This module also wires the demonstration of the core tool-hook bus: the
tool surface registers a ``post`` observer hook on the process-wide
:class:`~core.orchestration.hooks.ToolHookRegistry` matching ``fs_write``
that logs each verification outcome, and the ``fs_write`` handler
dispatches the matching post event after every successful write.
"""

from __future__ import annotations

import contextlib
import os
import py_compile
import tempfile
from typing import Any

from core.observability.logging import get_logger
from core.orchestration.hooks import (
    ToolHookEvent,
    ToolHookRegistry,
    get_tool_hook_registry,
)

logger = get_logger(__name__)

_ENV_FLAG = "BASELITH_POST_WRITE_VERIFY"
_FALSY = {"0", "false", "no", "off"}


def post_write_verify_default() -> bool:
    """Default for ``ComputerUseConfig.post_write_verify`` (env-driven, ON)."""
    return os.environ.get(_ENV_FLAG, "true").strip().lower() not in _FALSY


def compile_python_source(path: str) -> str | None:
    """Byte-compile ``path``; return the error message or ``None`` when clean.

    Runs ``py_compile.compile(..., doraise=True)`` with the bytecode routed
    to a throwaway temp file so no ``__pycache__`` artifact lands next to
    the agent-written file. Blocking — call via ``asyncio.to_thread``.

    Args:
        path: Absolute path of the ``.py`` file to check.

    Returns:
        ``None`` on success; the ``PyCompileError`` message (which embeds
        the offending line) on a syntax error.
    """
    fd, cfile = tempfile.mkstemp(suffix=".pyc")
    os.close(fd)
    try:
        py_compile.compile(path, cfile=cfile, doraise=True)
    except py_compile.PyCompileError as exc:
        return str(exc).strip()
    except Exception as exc:
        # Unexpected environmental failure (permissions, encoding probe...):
        # verification must never break a write that already succeeded.
        logger.warning("baselithbot_post_write_verify_error", path=path, error=str(exc))
        return None
    finally:
        with contextlib.suppress(OSError):
            os.unlink(cfile)
    return None


async def _log_fs_write_verification(event: ToolHookEvent) -> None:
    """Post-hook observer: log the verification outcome of one write."""
    logger.info(
        "baselithbot_fs_write_verified",
        tool=event.tool_name,
        ok=event.metadata.get("ok"),
        verification=event.metadata.get("verification"),
    )


_hooked_registry: ToolHookRegistry | None = None


def ensure_fs_write_hook() -> None:
    """Register the logging post-hook once per registry instance.

    Idempotent across repeated tool-surface rebuilds (the dashboard rebuilds
    the surface on every policy save); re-registers automatically after
    ``reset_tool_hook_registry()`` swaps the process-wide instance in tests.
    """
    global _hooked_registry
    registry = get_tool_hook_registry()
    if registry is _hooked_registry:
        return
    registry.register("post", "*fs_write", _log_fs_write_verification)
    _hooked_registry = registry


async def dispatch_fs_write_post(tool_name: str, result: dict[str, Any]) -> None:
    """Dispatch the ``post`` hook event for one successful ``fs_write``.

    Post hooks are observers by contract — the registry logs and swallows
    hook failures, so this can never break the write path.

    Args:
        tool_name: Public tool name (``baselithbot_fs_write``).
        result: The tool result about to be returned to the caller.
    """
    metadata: dict[str, Any] = {"ok": result.get("status") == "success"}
    if "verification" in result:
        metadata["verification"] = result["verification"]
    await get_tool_hook_registry().dispatch_post(
        ToolHookEvent(
            tool_name=tool_name,
            category="destructive",
            phase="post",
            metadata=metadata,
        )
    )


__all__ = [
    "compile_python_source",
    "dispatch_fs_write_post",
    "ensure_fs_write_hook",
    "post_write_verify_default",
]
