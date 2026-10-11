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

And one HTTP layer, so every list endpoint speaks the same contract:

* :func:`page_params` — a FastAPI dependency declaring ``limit``
  (``ge=1, le=MAX_LIMIT`` — the cap is visible in the OpenAPI schema) and
  ``cursor``.
* :func:`paginated` — renders a page as ``{<key>: [...], "count": <items in
  this page>, "next_cursor": <token or null>, "has_more": <bool>}`` and maps a
  bad cursor to a ``400`` problem document. The list key and ``count`` keep the
  shape the unpaginated endpoints had, so a client that ignores the two new
  members still works — it just sees the first page.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Annotated, Any

import orjson
from fastapi import HTTPException, Query
from pydantic import BaseModel, Field

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
#: Upper bound on an incoming cursor token. Real cursors are a few dozen bytes;
#: the bound keeps a multi-kilobyte query parameter out of the decoder.
MAX_CURSOR_LENGTH = 512


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


@dataclass(frozen=True, slots=True)
class PageParams:
    """The ``limit`` / ``cursor`` pair a list endpoint was called with."""

    limit: int | None = None
    cursor: str | None = None


def page_params(
    limit: Annotated[
        int | None,
        Query(
            ge=1,
            le=MAX_LIMIT,
            description=f"Page size (default {DEFAULT_LIMIT}, max {MAX_LIMIT}).",
        ),
    ] = None,
    cursor: Annotated[
        str | None,
        Query(
            max_length=MAX_CURSOR_LENGTH,
            description="Opaque `next_cursor` from the previous page.",
        ),
    ] = None,
) -> PageParams:
    """FastAPI dependency: the standard pagination query parameters."""
    return PageParams(limit=limit, cursor=cursor)


def _identity(item: Any) -> Any:
    return item


def paginated(
    items: Sequence[Any],
    page: PageParams,
    *,
    key: str,
    serialize: Callable[[Any], Any] = _identity,
) -> dict[str, Any]:
    """Render one page of ``items`` in the standard list-response shape.

    Only the page is serialised — never the whole collection.

    Raises:
        HTTPException: ``400`` when the cursor is malformed.
    """
    try:
        result = paginate_sequence(items, limit=page.limit, cursor=page.cursor)
    except PaginationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        key: [serialize(item) for item in result.items],
        "count": len(result.items),
        "next_cursor": result.next_cursor,
        "has_more": result.has_more,
    }


def cursor_offset(page: PageParams) -> int:
    """The offset a :func:`paginate_sequence` cursor encodes (``0`` without one).

    For endpoints that page over ids and fetch rows only for the page.

    Raises:
        HTTPException: ``400`` when the cursor is malformed.
    """
    if not page.cursor:
        return 0
    try:
        data = decode_cursor(page.cursor)
    except PaginationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    offset = data.get("offset", 0)
    if not isinstance(offset, int) or offset < 0:
        raise HTTPException(status_code=400, detail="Invalid pagination cursor")
    return offset
