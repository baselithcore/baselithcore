"""Apply a unified-diff patch to files under ``ScopedFileSystem``."""

from __future__ import annotations

import difflib
from typing import Any

from plugins.baselithbot.computer_use.config import ComputerUseError
from plugins.baselithbot.computer_use.filesystem import ScopedFileSystem


def _split_diff_files(diff_text: str) -> list[tuple[str, list[str]]]:
    """Split a unified diff into ``(filename, hunks)`` blocks."""
    files: list[tuple[str, list[str]]] = []
    current_file: str | None = None
    current_lines: list[str] = []

    for line in diff_text.splitlines(keepends=True):
        if line.startswith("--- "):
            if current_file is not None:
                files.append((current_file, current_lines))
                current_lines = []
            current_file = line[4:].rstrip().split("\t")[0]
            if current_file.startswith("a/"):
                current_file = current_file[2:]
        elif line.startswith("+++ "):
            target = line[4:].rstrip().split("\t")[0]
            if target.startswith("b/"):
                target = target[2:]
            current_file = target
        else:
            current_lines.append(line)
    if current_file is not None:
        files.append((current_file, current_lines))
    return files


def _apply_hunks_to_text(original: str, hunk_lines: list[str]) -> str:
    """Rebuild target text by interpreting ``@@`` hunks against ``original``."""
    src_lines = original.splitlines(keepends=True)
    out: list[str] = []
    src_idx = 0

    for line in hunk_lines:
        if line.startswith("@@"):
            try:
                header = line.split("@@")[1].strip()
                old_part = header.split(" ")[0]
                old_start = int(old_part.lstrip("-").split(",")[0]) - 1
            except (IndexError, ValueError) as exc:
                raise ComputerUseError(f"malformed hunk header: {line!r}") from exc
            out.extend(src_lines[src_idx:old_start])
            src_idx = old_start
        elif line.startswith("---") or line.startswith("+++"):
            continue
        elif line.startswith("+") and not line.startswith("+++"):
            out.append(line[1:])
        elif line.startswith("-") and not line.startswith("---"):
            src_idx += 1
        elif line.startswith(" "):
            if src_idx < len(src_lines):
                out.append(src_lines[src_idx])
            src_idx += 1
        elif line.startswith("\\"):
            continue

    out.extend(src_lines[src_idx:])
    return "".join(out)


async def apply_unified_diff(diff_text: str, fs: ScopedFileSystem) -> dict[str, Any]:
    """Apply a unified diff to every referenced file under ``fs``.

    Atomic per-file: each file is read, patched in memory, then written back.
    Failures abort the remaining files; already-written files are NOT rolled
    back (callers are expected to operate inside a workspace they trust).
    """
    blocks = _split_diff_files(diff_text)
    if not blocks:
        return {"status": "noop", "files": []}

    summary: list[dict[str, Any]] = []
    for filename, hunks in blocks:
        try:
            current = await fs.read(filename)
            updated_text = _apply_hunks_to_text(current["content"], hunks)
        except ComputerUseError as exc:
            summary.append({"path": filename, "status": "error", "error": str(exc)})
            break

        diff_preview = "".join(
            difflib.unified_diff(
                current["content"].splitlines(keepends=True),
                updated_text.splitlines(keepends=True),
                fromfile=f"a/{filename}",
                tofile=f"b/{filename}",
                n=2,
            )
        )
        write = await fs.write(filename, updated_text)
        summary.append(
            {
                "path": filename,
                "status": "patched",
                "bytes_written": write["bytes_written"],
                "preview": diff_preview[:1000],
            }
        )

    return {"status": "success", "files": summary, "count": len(summary)}


__all__ = ["apply_unified_diff"]
