"""Cursor-based pagination for the MCP list operations.

The spec models list pagination as an *opaque* cursor: the client echoes back
whatever the server sent and must never parse it. The cursor here encodes the
last key of the page just served, so paging stays correct even when entries are
registered or removed between requests — a positional index would silently skip
or repeat entries instead.

Keys are served in sorted order, which also gives clients a deterministic list
they can cache.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Iterable
from typing import Any

from core.mcp.errors import InvalidParams


def encode_cursor(key: str) -> str:
    """Encode *key* as an opaque cursor token."""
    return base64.urlsafe_b64encode(key.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> str:
    """Decode a cursor produced by :func:`encode_cursor`.

    Raises:
        InvalidParams: The token is not a cursor this server minted.
    """
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode()).decode()
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise InvalidParams(f"Invalid cursor: {cursor}") from exc


def paginate(
    keys: Iterable[str], cursor: str | None, page_size: int
) -> tuple[list[str], str | None]:
    """Return one page of *keys* plus the cursor for the page after it.

    Args:
        keys: All available keys, in any order (sorted internally).
        cursor: Token returned as ``nextCursor`` by the previous call, or None
            for the first page.
        page_size: Maximum entries per page.

    Returns:
        ``(page, next_cursor)`` — ``next_cursor`` is None on the last page.

    Raises:
        InvalidParams: The cursor is malformed or names a key this server does
            not know, which would otherwise silently restart the listing.
    """
    ordered = sorted(keys)
    start = 0
    if cursor is not None:
        last_seen = decode_cursor(cursor)
        if last_seen not in ordered:
            raise InvalidParams(f"Invalid cursor: {cursor}")
        start = ordered.index(last_seen) + 1

    page = ordered[start : start + page_size]
    exhausted = start + page_size >= len(ordered)
    next_cursor = None if exhausted or not page else encode_cursor(page[-1])
    return page, next_cursor


def page_registry(
    registry: dict[str, Any], params: dict[str, Any], config: Any
) -> tuple[list[Any], str | None]:
    """Slice a name-keyed registry into one page of entries.

    Args:
        registry: Name/URI → entry mapping to page over.
        params: Request params, read for an optional ``cursor``.
        config: Object carrying ``mcp_list_page_size`` (falls back to 100).

    Returns:
        ``(entries, next_cursor)``.
    """
    page_size = getattr(config, "mcp_list_page_size", 100)
    keys, next_cursor = paginate(registry, params.get("cursor"), page_size)
    return [registry[key] for key in keys], next_cursor


def with_cursor(result: dict[str, Any], next_cursor: str | None) -> dict[str, Any]:
    """Attach ``nextCursor`` only when another page exists."""
    if next_cursor is not None:
        result["nextCursor"] = next_cursor
    return result


__all__ = [
    "decode_cursor",
    "encode_cursor",
    "page_registry",
    "paginate",
    "with_cursor",
]
