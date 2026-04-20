"""Multi-file atomic editor with rollback on partial failure."""

from __future__ import annotations

from contextlib import suppress
from typing import Any

from pydantic import BaseModel

from ..filesystem import ScopedFileSystem


class MultiFileEdit(BaseModel):
    """Single file write request inside a batch."""

    path: str
    content: str


class MultiFileEditor:
    """Apply a batch of writes atomically; rollback on partial failure."""

    def __init__(self, fs: ScopedFileSystem) -> None:
        self._fs = fs

    async def apply(self, edits: list[MultiFileEdit]) -> dict[str, Any]:
        backups: list[tuple[str, str | None]] = []
        applied: list[dict[str, Any]] = []
        try:
            for edit in edits:
                try:
                    previous = await self._fs.read(edit.path)
                    backups.append((edit.path, previous["content"]))
                except Exception:
                    backups.append((edit.path, None))
                write = await self._fs.write(edit.path, edit.content)
                applied.append(
                    {
                        "path": edit.path,
                        "status": "written",
                        "bytes_written": write["bytes_written"],
                    }
                )
            return {"status": "success", "files": applied}
        except Exception as exc:
            for path, original in reversed(backups):
                if original is not None:
                    with suppress(Exception):
                        await self._fs.write(path, original)
            return {
                "status": "rolled_back",
                "error": str(exc),
                "applied_before_failure": applied,
                "rolled_back_files": [p for p, _ in backups],
            }


__all__ = ["MultiFileEdit", "MultiFileEditor"]
