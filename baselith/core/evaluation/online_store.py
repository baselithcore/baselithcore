"""Persistence of online evaluation results (opt-in).

:class:`OnlineEvalStore` is the boundary; :class:`InMemoryOnlineEvalStore` is a
bounded reference implementation (oldest records evicted) and
:class:`~baselith.core.evaluation.online_store_postgres.PostgresOnlineEvalStore`
the durable one (``EVAL_ONLINE_STORE=postgres``, table ``online_eval_results``
from migration 013).

A record holds the verdict, never the conversation: score, quality, aspects,
flags and signals, plus the intent, run id and tenant. The query and response
text are kept only when ``EVAL_ONLINE_STORE_RAW_TEXT=true``, and then only
after :func:`baselith.core.observability.redaction.redact_text` masked e-mail
addresses and credentials and the text was cut to :data:`MAX_STORED_TEXT`.
"""

from __future__ import annotations

import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from baselith.core.observability.redaction import redact_text

#: Characters of query/response text a record may keep (after redaction).
MAX_STORED_TEXT = 4000
#: Characters handed to the redactor: bounds its regex work on huge answers.
_REDACT_INPUT_CAP = 2 * MAX_STORED_TEXT


@dataclass(frozen=True, slots=True)
class OnlineEvalRecord:
    """One stored online evaluation result."""

    intent: str
    source: str
    score: float
    quality: str
    run_id: str | None = None
    tenant_id: str = "default"
    aspects: dict[str, float] = field(default_factory=dict)
    flags: tuple[str, ...] = ()
    signals: dict[str, float] = field(default_factory=dict)
    query_text: str | None = None
    response_text: str | None = None
    created_at: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def to_dict(self) -> dict[str, Any]:
        """A JSON-friendly view of the record."""
        return {
            "id": self.id,
            "intent": self.intent,
            "source": self.source,
            "score": self.score,
            "quality": self.quality,
            "run_id": self.run_id,
            "tenant_id": self.tenant_id,
            "aspects": dict(self.aspects),
            "flags": list(self.flags),
            "signals": dict(self.signals),
            "query_text": self.query_text,
            "response_text": self.response_text,
            "created_at": self.created_at,
        }


def stored_text(text: str | None) -> str | None:
    """Redact and cut *text* for storage; ``None`` stays ``None``."""
    if text is None:
        return None
    return redact_text(text[:_REDACT_INPUT_CAP])[:MAX_STORED_TEXT]


@runtime_checkable
class OnlineEvalStore(Protocol):
    """Persistence boundary for online evaluation results."""

    async def save(self, record: OnlineEvalRecord) -> None:
        """Persist one record."""
        ...

    async def recent(
        self,
        *,
        intent: str | None = None,
        limit: int = 100,
        tenant_id: str | None = None,
    ) -> list[OnlineEvalRecord]:
        """The newest records first, optionally for one intent and tenant."""
        ...

    async def purge_before(self, cutoff: float) -> int:
        """Delete records created before *cutoff* (unix seconds); return the count."""
        ...


class InMemoryOnlineEvalStore:
    """Bounded in-process store: the oldest records are evicted first.

    Args:
        max_records: Records kept before eviction.
    """

    def __init__(self, max_records: int = 10_000) -> None:
        self._records: deque[OnlineEvalRecord] = deque(maxlen=max(1, max_records))

    async def save(self, record: OnlineEvalRecord) -> None:
        self._records.append(record)

    async def recent(
        self,
        *,
        intent: str | None = None,
        limit: int = 100,
        tenant_id: str | None = None,
    ) -> list[OnlineEvalRecord]:
        rows = [
            r
            for r in reversed(self._records)
            if (intent is None or r.intent == intent)
            and (tenant_id is None or r.tenant_id == tenant_id)
        ]
        return rows[: max(0, limit)]

    async def purge_before(self, cutoff: float) -> int:
        kept = [r for r in self._records if r.created_at >= cutoff]
        removed = len(self._records) - len(kept)
        self._records.clear()
        self._records.extend(kept)
        return removed

    def __len__(self) -> int:
        return len(self._records)


def build_online_store(
    kind: str, *, max_records: int = 10_000
) -> OnlineEvalStore | None:
    """Build the store named by ``EVAL_ONLINE_STORE``.

    Args:
        kind: ``none``, ``memory`` or ``postgres``.
        max_records: Capacity of the in-memory store.

    Returns:
        The store, or ``None`` for ``none``.

    Raises:
        ValueError: For an unknown kind.
    """
    if kind == "none":
        return None
    if kind == "memory":
        return InMemoryOnlineEvalStore(max_records)
    if kind == "postgres":
        # psycopg is imported only when the durable store is asked for.
        from baselith.core.evaluation.online_store_postgres import (
            PostgresOnlineEvalStore,
        )

        return PostgresOnlineEvalStore()
    raise ValueError(f"Unknown online evaluation store: {kind!r}")


__all__ = [
    "MAX_STORED_TEXT",
    "InMemoryOnlineEvalStore",
    "OnlineEvalRecord",
    "OnlineEvalStore",
    "build_online_store",
    "stored_text",
]
