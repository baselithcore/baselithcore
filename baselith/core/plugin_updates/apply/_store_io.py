"""Shared file helpers of the run store: ids, atomic writes, tolerant reads."""

from __future__ import annotations

import os
import re
import secrets
from datetime import UTC, datetime
from pathlib import Path

_RUN_ID = re.compile(r"^pinstall-\d{8}T\d{6}Z-[0-9a-f]{8}$")
_PLUGIN = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        tmp.unlink(missing_ok=True)


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


__all__: list[str] = []
