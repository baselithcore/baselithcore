"""PostgreSQL + pgvector vector store provider.

Second concrete backend for :class:`~core.services.vectorstore.interfaces.VectorStoreProtocol`
— for stacks that already run Postgres and don't want a dedicated vector
database. One table per collection (``vs_<name>``) with an HNSW cosine index;
search scores are cosine similarity (``1 - (embedding <=> query)``), matching
Qdrant's default metric so providers are interchangeable.

Results are lightweight :class:`PgVectorPoint` objects duck-compatible with
Qdrant hits: consumers already read ``hit.id`` / ``hit.score`` /
``getattr(hit, "payload", {})``.

Requires the ``vector`` extension in the target database
(``CREATE EXTENSION IF NOT EXISTS vector`` is attempted at collection
creation; it needs sufficient privileges once per database). No new Python
dependency — vectors cross the wire in pgvector's text encoding.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import orjson
from psycopg.rows import dict_row

from core.db.connection import get_async_cursor
from core.observability.logging import get_logger
from core.services.vectorstore.exceptions import VectorStoreError

logger = get_logger(__name__)

_NAME_RE = re.compile(r"^[a-zA-Z0-9_]+$")


def _table(collection_name: str) -> str:
    """Sanitized physical table name for a collection (defense in depth).

    The returned identifier is the ONLY dynamic fragment ever interpolated
    into this module's SQL (all values go through bind parameters). It is
    strictly ``vs_`` + ``[a-zA-Z0-9_]+`` — no quotes, spaces, or separators
    can pass — so the ``B608`` findings on those f-strings are false
    positives, suppressed at each site with ``# nosec B608``.
    """
    if not _NAME_RE.match(collection_name or ""):
        raise VectorStoreError(
            f"Invalid collection name {collection_name!r}: only [a-zA-Z0-9_] allowed."
        )
    return f"vs_{collection_name.lower()}"


def _encode_vector(vector: Sequence[float]) -> str:
    """pgvector text encoding: '[0.1,0.2,...]'."""
    return "[" + ",".join(repr(float(v)) for v in vector) + "]"


@dataclass(frozen=True)
class PgVectorPoint:
    """A stored/retrieved point, duck-compatible with Qdrant hit objects."""

    id: str
    payload: dict[str, Any] = field(default_factory=dict)
    score: float | None = None
    vector: list[float] | None = None


def _row_payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("payload") or {}
    if isinstance(payload, str):
        payload = orjson.loads(payload)
    return payload


def _condition_sql(condition: Any, params: list[Any], negate: bool) -> str:
    """Translate one qdrant-style FieldCondition to a payload predicate.

    Duck-typed on ``.key`` + ``.match`` (with ``.value`` or ``.any``) so no
    qdrant-client import is needed — callers like the chat retrieval mixins
    keep passing real ``qdrant_client.models`` objects unchanged.
    """
    key = getattr(condition, "key", None)
    match = getattr(condition, "match", None)
    if key is None or match is None:
        raise VectorStoreError(
            f"Unsupported filter condition for pgvector: {condition!r} "
            "(expected a FieldCondition-like object with .key and .match)."
        )
    any_values = getattr(match, "any", None)
    if any_values is not None:
        params.extend([str(key), [str(v) for v in any_values]])
        fragment = "payload->>%s = ANY(%s)"
    else:
        params.extend([str(key), str(getattr(match, "value", None))])
        fragment = "payload->>%s = %s"
    return f"NOT ({fragment})" if negate else fragment


def _filter_where(
    filter_obj: Any, tenant_id: str | None, params: list[Any]
) -> list[str]:
    """WHERE fragments for a tenant + a dict or qdrant-style filter.

    Mirrors the Qdrant provider's semantics: the tenant condition is always
    ANDed in when a ``tenant_id`` is given, on top of whatever filter the
    caller supplied.
    """
    where: list[str] = []
    if tenant_id:
        where.append("payload @> %s::jsonb")
        params.append(orjson.dumps({"tenant_id": tenant_id}).decode())
    if filter_obj is None:
        return where
    if isinstance(filter_obj, dict):
        if filter_obj:
            where.append("payload @> %s::jsonb")
            params.append(orjson.dumps(filter_obj).decode())
        return where
    must = getattr(filter_obj, "must", None)
    must_not = getattr(filter_obj, "must_not", None)
    if must is None and must_not is None:
        raise VectorStoreError(
            f"Unsupported filter type for pgvector: {type(filter_obj).__name__} "
            "(expected a dict or a Filter-like object with .must/.must_not)."
        )
    for group, negate in ((must, False), (must_not, True)):
        if group is None:
            continue
        conditions = group if isinstance(group, (list, tuple)) else [group]
        where.extend(_condition_sql(c, params, negate) for c in conditions)
    return where


class PgVectorProvider:
    """Vector store provider backed by PostgreSQL + pgvector."""

    async def create_collection(
        self, collection_name: str, vector_size: int, **kwargs: Any
    ) -> None:
        """Create the collection table, extension, and HNSW index (idempotent)."""
        table = _table(collection_name)
        size = int(vector_size)
        ddl = (
            "CREATE EXTENSION IF NOT EXISTS vector;\n"
            f"CREATE TABLE IF NOT EXISTS {table} (\n"
            "    id TEXT PRIMARY KEY,\n"
            f"    embedding vector({size}) NOT NULL,\n"
            "    payload JSONB NOT NULL DEFAULT '{}'::jsonb\n"
            ");\n"
            f"CREATE INDEX IF NOT EXISTS idx_{table}_hnsw ON {table} "
            "USING hnsw (embedding vector_cosine_ops);\n"
            # jsonb_path_ops: smaller/faster GIN variant that supports exactly
            # the @> containment operator every tenant-scoped search ANDs in —
            # without it multi-tenant filtering seq-scans the payload column.
            f"CREATE INDEX IF NOT EXISTS idx_{table}_payload_gin ON {table} "
            "USING gin (payload jsonb_path_ops);"
        )
        try:
            async with get_async_cursor() as cur:
                await cur.execute(ddl)
        except VectorStoreError:
            raise
        except Exception as exc:
            raise VectorStoreError(
                f"pgvector collection creation failed for {collection_name!r}: {exc}"
            ) from exc
        logger.info(f"pgvector collection '{collection_name}' ready ({table})")

    async def upsert(
        self, collection_name: str, points: list[dict[str, Any]], **kwargs: Any
    ) -> None:
        """Upsert points (dicts with ``id``, ``vector``, optional ``payload``)."""
        table = _table(collection_name)
        sql = (
            f"INSERT INTO {table} (id, embedding, payload) "  # nosec B608
            "VALUES (%s, %s::vector, %s::jsonb) "
            "ON CONFLICT (id) DO UPDATE SET "
            "embedding = EXCLUDED.embedding, payload = EXCLUDED.payload"
        )
        if not points:
            return
        rows = [
            (
                str(point["id"]),
                _encode_vector(point["vector"]),
                orjson.dumps(point.get("payload", {})).decode(),
            )
            for point in points
        ]
        # executemany: psycopg pipelines the whole batch instead of paying one
        # network round-trip per point on a single connection checkout.
        async with get_async_cursor() as cur:
            await cur.executemany(sql, rows)
        logger.debug(f"Upserted {len(points)} points to '{collection_name}'")

    async def search(
        self,
        collection_name: str,
        query_vector: Sequence[float],
        limit: int = 10,
        **kwargs: Any,
    ) -> list[Any]:
        """Cosine-similarity search.

        Supported kwargs: ``tenant_id`` (always ANDed in — same isolation
        semantics as the Qdrant provider), ``query_filter``/``filter`` (a
        payload-equality dict or a qdrant-style Filter with
        must/must_not FieldConditions), and ``score_threshold`` (minimum
        similarity). Other kwargs (e.g. ``with_payload``) are ignored.
        """
        table = _table(collection_name)
        encoded = _encode_vector(query_vector)
        params: list[Any] = [encoded]
        where = _filter_where(
            kwargs.get("query_filter", kwargs.get("filter")),
            kwargs.get("tenant_id"),
            params,
        )
        score_threshold = kwargs.get("score_threshold")
        if score_threshold is not None:
            where.append("(1 - (embedding <=> %s::vector)) >= %s")
            params.extend([encoded, float(score_threshold)])
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        sql = (
            "SELECT id, payload, 1 - (embedding <=> %s::vector) AS score "  # nosec B608
            f"FROM {table} {where_sql} "
            "ORDER BY embedding <=> %s::vector LIMIT %s"
        )
        params.extend([encoded, int(limit)])
        async with get_async_cursor(row_factory=dict_row) as cur:  # type: ignore
            await cur.execute(sql, params)
            rows = await cur.fetchall()
        return [
            PgVectorPoint(
                id=row["id"], payload=_row_payload(row), score=float(row["score"])
            )
            for row in rows
            if isinstance(row, dict)
        ]

    async def retrieve(
        self, collection_name: str, point_ids: list[int | str], **kwargs: Any
    ) -> list[Any]:
        """Retrieve points by id, tenant-scoped when ``tenant_id`` is given."""
        table = _table(collection_name)
        params: list[Any] = [[str(pid) for pid in point_ids]]
        where = ["id = ANY(%s)"]
        where += _filter_where(None, kwargs.get("tenant_id"), params)
        sql = (
            f"SELECT id, payload FROM {table} "  # nosec B608
            f"WHERE {' AND '.join(where)}"
        )
        async with get_async_cursor(row_factory=dict_row) as cur:  # type: ignore
            await cur.execute(sql, params)
            rows = await cur.fetchall()
        return [
            PgVectorPoint(id=row["id"], payload=_row_payload(row))
            for row in rows
            if isinstance(row, dict)
        ]

    async def delete(
        self, collection_name: str, point_ids: list[int | str], **kwargs: Any
    ) -> None:
        """Delete points by id."""
        table = _table(collection_name)
        async with get_async_cursor() as cur:
            await cur.execute(
                f"DELETE FROM {table} WHERE id = ANY(%s)",  # nosec B608
                ([str(pid) for pid in point_ids],),
            )

    async def scroll(
        self,
        collection_name: str,
        limit: int = 100,
        offset: int | str | None = None,
        **kwargs: Any,
    ) -> Any:
        """Keyset-paginate points; returns ``(points, next_offset)``.

        ``next_offset`` is the last id of a full page (pass it back to
        continue), or ``None`` when the collection is exhausted — the same
        contract as Qdrant's scroll. Supports ``tenant_id`` and
        ``scroll_filter``/``filter`` (dict or qdrant-style Filter), matching
        the Qdrant provider's semantics.
        """
        table = _table(collection_name)
        params: list[Any] = []
        where: list[str] = []
        if offset is not None:
            where.append("id > %s")
            params.append(str(offset))
        where += _filter_where(
            kwargs.get("scroll_filter", kwargs.get("filter")),
            kwargs.get("tenant_id"),
            params,
        )
        where_sql = f"WHERE {' AND '.join(where)} " if where else ""
        sql = f"SELECT id, payload FROM {table} {where_sql}ORDER BY id LIMIT %s"  # nosec B608
        params.append(int(limit))
        async with get_async_cursor(row_factory=dict_row) as cur:  # type: ignore
            await cur.execute(sql, params)
            rows = await cur.fetchall()
        points = [
            PgVectorPoint(id=row["id"], payload=_row_payload(row))
            for row in rows
            if isinstance(row, dict)
        ]
        next_offset = points[-1].id if len(points) == int(limit) and points else None
        return points, next_offset

    async def delete_by_filter(
        self, collection_name: str, key: str, value: Any, **kwargs: Any
    ) -> None:
        """Delete points whose payload ``key`` equals ``value`` (tenant-scoped).

        A ``list``/``tuple``/``set`` value means "match ANY of these values" —
        one statement for a whole batch instead of one DELETE per value.
        """
        table = _table(collection_name)
        if isinstance(value, (list, tuple, set, frozenset)):
            params: list[Any] = [key, [str(v) for v in value]]
            where = ["payload->>%s = ANY(%s)"]
        else:
            params = [key, str(value)]
            where = ["payload->>%s = %s"]
        where += _filter_where(None, kwargs.get("tenant_id"), params)
        async with get_async_cursor() as cur:
            await cur.execute(
                f"DELETE FROM {table} "  # nosec B608
                f"WHERE {' AND '.join(where)}",
                params,
            )


__all__ = ["PgVectorPoint", "PgVectorProvider"]
