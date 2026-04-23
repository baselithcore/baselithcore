"""Line-range edits over files in ``ScopedFileSystem``."""

from __future__ import annotations

from typing import Any

from plugins.baselithbot.computer_use.config import ComputerUseError
from plugins.baselithbot.computer_use.filesystem import ScopedFileSystem
from pydantic import BaseModel, Field


class LineRangeEdit(BaseModel):
    """Replace ``[start_line, end_line]`` (1-indexed, inclusive) with text."""

    path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    replacement: str = ""


class LineRangePatcher:
    """Apply a list of ``LineRangeEdit`` operations atomically per file."""

    def __init__(self, fs: ScopedFileSystem) -> None:
        self._fs = fs

    async def apply(self, edits: list[LineRangeEdit]) -> dict[str, Any]:
        grouped: dict[str, list[LineRangeEdit]] = {}
        for edit in edits:
            if edit.end_line < edit.start_line:
                raise ComputerUseError(
                    f"end_line < start_line for {edit.path}: {edit.end_line} < {edit.start_line}"
                )
            grouped.setdefault(edit.path, []).append(edit)

        out: list[dict[str, Any]] = []
        for path, file_edits in grouped.items():
            current = await self._fs.read(path)
            lines = current["content"].splitlines(keepends=True)
            file_edits_sorted = sorted(file_edits, key=lambda e: e.start_line, reverse=True)
            for edit in file_edits_sorted:
                start = edit.start_line - 1
                end = edit.end_line
                if start >= len(lines):
                    raise ComputerUseError(f"start_line {edit.start_line} out of range for {path}")
                replacement_lines = (
                    edit.replacement.splitlines(keepends=True) if edit.replacement else []
                )
                if (
                    replacement_lines
                    and not replacement_lines[-1].endswith("\n")
                    and end < len(lines)
                ):
                    replacement_lines[-1] = replacement_lines[-1] + "\n"
                lines[start:end] = replacement_lines
            updated = "".join(lines)
            write = await self._fs.write(path, updated)
            out.append(
                {
                    "path": path,
                    "status": "patched",
                    "edits_applied": len(file_edits),
                    "bytes_written": write["bytes_written"],
                }
            )
        return {"status": "success", "files": out}


__all__ = ["LineRangeEdit", "LineRangePatcher"]
