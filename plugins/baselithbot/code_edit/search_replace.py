"""Search/replace edits over a single file (literal or regex)."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from ..computer_use import ComputerUseError
from ..filesystem import ScopedFileSystem


class SearchReplaceEdit(BaseModel):
    path: str
    pattern: str
    replacement: str
    regex: bool = False
    count: int = Field(default=0, ge=0)
    case_insensitive: bool = False


async def apply_search_replace(edit: SearchReplaceEdit, fs: ScopedFileSystem) -> dict[str, Any]:
    current = await fs.read(edit.path)
    text: str = current["content"]

    if edit.regex:
        try:
            flags = re.IGNORECASE if edit.case_insensitive else 0
            new_text, n = re.subn(
                edit.pattern, edit.replacement, text, count=edit.count, flags=flags
            )
        except re.error as exc:
            raise ComputerUseError(f"invalid regex: {exc}") from exc
    else:
        if edit.case_insensitive:
            pattern = re.compile(re.escape(edit.pattern), re.IGNORECASE)
            new_text, n = pattern.subn(edit.replacement, text, count=edit.count)
        else:
            n = (
                text.count(edit.pattern)
                if edit.count == 0
                else min(text.count(edit.pattern), edit.count)
            )
            if edit.count == 0:
                new_text = text.replace(edit.pattern, edit.replacement)
            else:
                new_text = text.replace(edit.pattern, edit.replacement, edit.count)

    if n == 0:
        return {"status": "noop", "path": edit.path, "matches": 0}

    write = await fs.write(edit.path, new_text)
    return {
        "status": "success",
        "path": edit.path,
        "matches": n,
        "bytes_written": write["bytes_written"],
    }


__all__ = ["SearchReplaceEdit", "apply_search_replace"]
