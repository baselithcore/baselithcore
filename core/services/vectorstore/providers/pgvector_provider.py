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
    """Sanitized physical table name for a collection (defense in depth)."""
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
            "USING hnsw (embedding vector_cosine_ops);"
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
            f"INSERT INTO {table} (id, embedding, payload) "
            "VALUES (%s, %s::vector, %s::jsonb) "
            "ON CONFLICT (id) DO UPDATE SET "
            "embedding = EXCLUDED.embedding, payload = EXCLUDED.payload"
        )
        async with get_async_cursor() as cur:
            for point in points:
                await cur.execute(
                    sql,
                    (
                        str(point["id"]),
                        _encode_vector(point["vector"]),
                        orjson.dumps(point.get("payload", {})).decode(),
                    ),
                )
        logger.debug(f"Upserted {len(points)} points to '{collection_name}'")

    async def search(
        self,
        collection_name: str,
        query_vector: Sequence[float],
        limit: int = 10,
        **kwargs: Any,
    ) -> list[Any]:
        """Cosine-similarity search.

        Supported kwargs: ``score_threshold`` (minimum similarity) and
        ``filter`` (dict of payload key → value, all must match). Other
        kwargs (e.g. Qdrant's ``with_payload``) are ignored.
        """
        table = _table(collection_name)
        encoded = _encode_vector(query_vector)
        where: list[str] = []
        params: list[Any] = [encoded]
        filter_dict = kwargs.get("filter")
        if isinstance(filter_dict, dict) and filter_dict:
            where.append("payload @> %s::jsonb")
            params.append(orjson.dumps(filter_dict).decode())
        score_threshold = kwargs.get("score_threshold")
        if score_threshold is not None:
            where.append("(1 - (embedding <=> %s::vector)) >= %s")
            params.extend([encoded, float(score_threshold)])
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        sql = (
            "SELECT id, payload, 1 - (embedding <=> %s::vector) AS score "
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
        """Retrieve points by id (payload always included)."""
        table = _table(collection_name)
        sql = f"SELECT id, payload FROM {table} WHERE id = ANY(%s)"
        async with get_async_cursor(row_factory=dict_row) as cur:  # type: ignore
            await cur.execute(sql, ([str(pid) for pid in point_ids],))
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
                f"DELETE FROM {table} WHERE id = ANY(%s)",
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
        contract as Qdrant's scroll.
        """
        table = _table(collection_name)
        params: list[Any] = []
        where_sql = ""
        if offset is not None:
            where_sql = "WHERE id > %s "
            params.append(str(offset))
        sql = f"SELECT id, payload FROM {table} {where_sql}ORDER BY id LIMIT %s"
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
        """Delete all points whose payload ``key`` equals ``value``."""
        table = _table(collection_name)
        async with get_async_cursor() as cur:
            await cur.execute(
                f"DELETE FROM {table} WHERE payload->>%s = %s",
                (key, str(value)),
            )


__all__ = ["PgVectorPoint", "PgVectorProvider"]
