"""The operator's own upgrade procedure (installation method ``custom``)."""

from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

logger = logging.getLogger(__name__)

#: The largest instructions file read; anything longer is refused.
MAX_INSTRUCTIONS_BYTES = 64 * 1024

#: The last file read: its path, identity (inode, mtime, size) and content.
_last_read: tuple[Path, tuple[int, int, int], bytes] | None = None


class _NotRegularFileError(OSError):
    """The configured path is not a regular file (a FIFO, a directory ...)."""


def _identity(st: os.stat_result) -> tuple[int, int, int]:
    return st.st_ino, st.st_mtime_ns, st.st_size


def _read_regular_file(path: Path) -> bytes:
    """Up to ``MAX_INSTRUCTIONS_BYTES + 1`` bytes of a regular file.

    Opened non-blocking and checked with ``fstat`` before any read, so a FIFO
    or a device can never stall the event loop that serves the notice.
    """
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise _NotRegularFileError(str(path))
        return handle.read(MAX_INSTRUCTIONS_BYTES + 1)


def _load(path: Path) -> bytes:
    """The file's bytes, re-read only when its inode, mtime or size changed."""
    global _last_read
    st = path.stat()
    if not stat.S_ISREG(st.st_mode):
        raise _NotRegularFileError(str(path))
    key = _identity(st)
    cached = _last_read
    if cached is not None and cached[0] == path and cached[1] == key:
        return cached[2]
    raw = _read_regular_file(path)
    _last_read = (path, key, raw)
    return raw


def render_instructions(
    path: Path | None, *, version: str, current: str
) -> tuple[str | None, str | None]:
    """Read the operator's instructions file and fill in the placeholders.

    ``{version}`` becomes the target release and ``{current}`` the installed
    one; nothing else is interpreted (no format specs, no templates). The text
    is returned as is otherwise: the client must show it without raw HTML.
    Only a regular file is read, and a read is reused until the file changes.

    Args:
        path: ``SYSTEM_UPGRADE_INSTRUCTIONS_FILE``, or None.
        version: The target public core release.
        current: The installed public core release.

    Returns:
        ``(text, None)`` on success, ``(None, error)`` when the file is set but
        cannot be used, ``(None, None)`` when no file is configured.
    """
    if path is None:
        return None, None
    try:
        raw = _load(path)
    except _NotRegularFileError:
        return None, "the upgrade instructions file is not a regular file"
    except OSError as exc:
        logger.warning("upgrade_instructions_unreadable: %s", type(exc).__name__)
        return None, "the upgrade instructions file cannot be read"
    if len(raw) > MAX_INSTRUCTIONS_BYTES:
        return None, "the upgrade instructions file is larger than 64 KiB"
    text = raw.decode("utf-8", errors="replace").replace("\x00", "")
    text = text.replace("{version}", version).replace("{current}", current)
    return text, None


__all__ = ["MAX_INSTRUCTIONS_BYTES", "render_instructions"]
