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


__all__ = ["decode_cursor", "encode_cursor", "paginate"]
