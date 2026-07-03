"""
Cursor-based pagination primitives.

A reusable, opaque-cursor pagination helper for list endpoints. Cursors are
base64url-encoded JSON — opaque to clients (they must not parse or construct
them), so the server can evolve the encoding (offset today, keyset tomorrow)
without breaking callers.

Two layers are provided:

* :func:`encode_cursor` / :func:`decode_cursor` — the opaque token codec, for
  custom (e.g. keyset) pagination.
* :func:`paginate_sequence` — offset-style pagination over a materialized
  sequence, suitable for in-memory stores; returns a :class:`CursorPage`.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Sequence
from typing import Any

import orjson
from pydantic import BaseModel, Field

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


class PaginationError(ValueError):
    """A pagination parameter (limit or cursor) was invalid."""


def encode_cursor(payload: dict[str, Any]) -> str:
    """Encode a cursor payload into an opaque base64url token."""
    raw = orjson.dumps(payload)
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> dict[str, Any]:
    """Decode an opaque cursor token. Raises :class:`PaginationError` if malformed."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        data = orjson.loads(raw)
    except (binascii.Error, ValueError, orjson.JSONDecodeError) as e:
        raise PaginationError("Invalid pagination cursor") from e
    if not isinstance(data, dict):
        raise PaginationError("Invalid pagination cursor")
    return data


def normalize_limit(limit: int | None, *, max_limit: int = MAX_LIMIT) -> int:
    """Clamp a requested limit into ``[1, max_limit]`` (default when ``None``)."""
    if limit is None:
        return min(DEFAULT_LIMIT, max_limit)
    if limit < 1:
        raise PaginationError("limit must be >= 1")
    return min(limit, max_limit)


class CursorPage(BaseModel):
    """A page of results with an opaque continuation cursor."""

    items: list[Any] = Field(default_factory=list)
    next_cursor: str | None = None
    has_more: bool = False
    limit: int = DEFAULT_LIMIT


def paginate_sequence(
    items: Sequence[Any],
    *,
    limit: int | None = None,
    cursor: str | None = None,
    max_limit: int = MAX_LIMIT,
) -> CursorPage:
    """Offset-paginate a materialized sequence with an opaque cursor.

    The cursor encodes the next offset. A page returns ``limit`` items and a
    ``next_cursor`` when more remain. Suitable for in-memory / already-fetched
    collections; for large datasets prefer keyset pagination over the DB using
    :func:`encode_cursor` / :func:`decode_cursor` directly.

    Raises:
        PaginationError: If ``limit`` or ``cursor`` is invalid.
    """
    eff_limit = normalize_limit(limit, max_limit=max_limit)
    offset = 0
    if cursor:
        data = decode_cursor(cursor)
        raw_offset = data.get("offset", 0)
        if not isinstance(raw_offset, int) or raw_offset < 0:
            raise PaginationError("Invalid pagination cursor")
        offset = raw_offset

    window = list(items[offset : offset + eff_limit])
    has_more = offset + eff_limit < len(items)
    next_cursor = encode_cursor({"offset": offset + eff_limit}) if has_more else None
    return CursorPage(
        items=window, next_cursor=next_cursor, has_more=has_more, limit=eff_limit
    )
