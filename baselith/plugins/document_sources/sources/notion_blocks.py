"""Notion blocks to markdown.

Pure functions over the JSON the Notion API returns, kept apart from the HTTP
code so they can be tested without a network. Only the text a reader would
see is kept: formatting that does not survive indexing (colours, annotations)
and media that has no text (images, files, embeds of private content) are
dropped, and a block type this module does not know is skipped, never guessed
at.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = [
    "UUID_PATTERN",
    "block_to_lines",
    "normalise_id",
    "page_title",
    "rich_text",
]

#: A Notion id as it appears in an API path: 32 hex characters, dashes optional.
UUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}$"
)

_HEADINGS = {"heading_1": "#", "heading_2": "##", "heading_3": "###"}
#: Block types whose children carry the content, with no text of their own.
_CONTAINERS = frozenset({"column_list", "column", "synced_block", "template"})
_TEXT_TYPES = frozenset({"paragraph", "toggle"})


def normalise_id(raw: object) -> str | None:
    """A Notion id in canonical dashed form, or ``None`` when it is not one.

    Ids are interpolated into API paths, so anything that is not exactly a UUID
    is refused rather than escaped.
    """
    if not isinstance(raw, str) or not UUID_PATTERN.match(raw.strip()):
        return None
    compact = raw.strip().replace("-", "").lower()
    return f"{compact[:8]}-{compact[8:12]}-{compact[12:16]}-{compact[16:20]}-{compact[20:]}"


def rich_text(items: Any) -> str:
    """The plain text of a rich-text array; links are kept as markdown."""
    parts: list[str] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("plain_text") or "")
        href = item.get("href")
        if text and isinstance(href, str) and href.startswith(("http://", "https://")):
            text = f"[{text}]({href})"
        parts.append(text)
    return "".join(parts)


def page_title(page: dict[str, Any]) -> str:
    """A page's title, from whichever property has type ``title``."""
    properties = page.get("properties")
    if isinstance(properties, dict):
        for prop in properties.values():
            if isinstance(prop, dict) and prop.get("type") == "title":
                return rich_text(prop.get("title")).strip()
    return ""


def _payload(block: dict[str, Any]) -> dict[str, Any]:
    data = block.get(str(block.get("type")))
    return data if isinstance(data, dict) else {}


def block_to_lines(block: dict[str, Any], depth: int = 0) -> list[str]:
    """The markdown lines of one block, without its children.

    Args:
        block: A block object from ``/blocks/{id}/children``.
        depth: Nesting level, used to indent list items.

    Returns:
        Zero or more lines; empty for containers, media and unknown types.
    """
    kind = str(block.get("type") or "")
    data = _payload(block)
    text = rich_text(data.get("rich_text")).strip()
    indent = "  " * depth

    if kind in _HEADINGS:
        return [f"{_HEADINGS[kind]} {text}"] if text else []
    if kind == "bulleted_list_item":
        return [f"{indent}- {text}"] if text else []
    if kind == "numbered_list_item":
        return [f"{indent}1. {text}"] if text else []
    if kind == "to_do":
        mark = "x" if data.get("checked") else " "
        return [f"{indent}- [{mark}] {text}"] if text else []
    if kind in ("quote", "callout"):
        return [f"> {line}" for line in text.splitlines()] if text else []
    if kind == "code":
        language = str(data.get("language") or "")
        return [f"```{language}", text, "```"] if text else []
    if kind == "divider":
        return ["---"]
    if kind == "equation":
        expression = str(data.get("expression") or "").strip()
        return [f"$$ {expression} $$"] if expression else []
    if kind in ("bookmark", "link_preview", "embed"):
        url = str(data.get("url") or "")
        return [url] if url.startswith(("http://", "https://")) else []
    if kind == "table_row":
        cells = data.get("cells") or []
        row = " | ".join(rich_text(cell).strip() for cell in cells)
        return [f"| {row} |"] if row.strip(" |") else []
    if kind in _TEXT_TYPES:
        return [f"{indent}{text}"] if text else []
    return []  # containers, media, child pages/databases, anything unknown
