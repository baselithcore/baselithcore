"""Durable SQLite backends for the compliance subsystem.

The in-memory reference stores survive only for the process lifetime, which is
the wrong property for records an authority can ask to see years later: the AI
system registry, Annex IV technical documentation (retained 10 years under
Art. 18), Art. 27 FRIAs and the Art. 30 ROPA are all long-lived artefacts.

Storage follows the same rationale as :mod:`core.incidents.persistence` —
stdlib :mod:`sqlite3`, one JSON blob per record keyed by id, no new
dependencies, and a Protocol boundary that a Postgres implementation can take
over without touching service code. ``check_same_thread=False`` plus an internal
:class:`~threading.RLock` makes each store safe to share across the event loop
and worker threads; ``PRAGMA journal_mode=WAL`` keeps concurrent reads
non-blocking.

Every statement runs on a worker thread via :func:`asyncio.to_thread`: SQLite is
blocking disk I/O, and issuing it from a coroutine stalls the whole event loop
(every in-flight request with it) for the duration of the write. One
``to_thread`` hop covers a complete unit of work — lock, statement, fetch, JSON
decode and domain rehydration — so the round-trip cost is paid once, not once
per step.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from threading import RLock
from typing import Any

from core.compliance.annex_iv import TechnicalDocumentation
from core.compliance.dpia import DataProtectionImpactAssessment
from core.compliance.fria import FundamentalRightsImpactAssessment
from core.compliance.instructions import InstructionsForUse
from core.compliance.post_market import PostMarketMonitoringPlan
from core.compliance.risk_management import RiskManagementSystem
from core.compliance.ropa import ProcessingActivity
from core.compliance.types import AiSystem


class _SQLiteJsonStore:
    """Single-table ``(id TEXT PRIMARY KEY, data TEXT)`` JSON store over SQLite."""

    _TABLE = "records"

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self._path), check_same_thread=False, isolation_level=None
        )
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._conn.execute(
            f"CREATE TABLE IF NOT EXISTS {self._TABLE} "
            "(id TEXT PRIMARY KEY, data TEXT NOT NULL);"
        )
        self._lock = RLock()

    # -- Blocking units of work (run on a worker thread) -------------------

    def _upsert(self, key: str, payload: dict[str, Any]) -> None:
        blob = json.dumps(payload, sort_keys=True, default=str)
        with self._lock:
            self._conn.execute(
                f"INSERT INTO {self._TABLE} (id, data) VALUES (?, ?) "  # nosec B608
                "ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                (key, blob),
            )

    def _fetch[T](self, key: str, factory: Callable[[dict[str, Any]], T]) -> T | None:
        with self._lock:
            cur = self._conn.execute(
                f"SELECT data FROM {self._TABLE} WHERE id = ?",  # nosec B608
                (key,),
            )
            row = cur.fetchone()
        return factory(json.loads(row[0])) if row is not None else None

    def _fetch_all[T](self, factory: Callable[[dict[str, Any]], T]) -> list[T]:
        with self._lock:
            cur = self._conn.execute(
                f"SELECT data FROM {self._TABLE} ORDER BY id ASC"  # nosec B608
            )
            rows = cur.fetchall()
        return [factory(json.loads(r[0])) for r in rows]

    def _delete(self, key: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                f"DELETE FROM {self._TABLE} WHERE id = ?",  # nosec B608
                (key,),
            )
            return max(cur.rowcount, 0) > 0

    # -- Async surface: exactly one ``to_thread`` hop per operation ---------

    async def _save(self, key: str, payload: dict[str, Any]) -> None:
        await asyncio.to_thread(self._upsert, key, payload)

    async def _load[T](
        self, key: str, factory: Callable[[dict[str, Any]], T]
    ) -> T | None:
        return await asyncio.to_thread(self._fetch, key, factory)

    async def _load_all[T](self, factory: Callable[[dict[str, Any]], T]) -> list[T]:
        return await asyncio.to_thread(self._fetch_all, factory)

    async def _remove(self, key: str) -> bool:
        return await asyncio.to_thread(self._delete, key)

    def close(self) -> None:
        """Close the underlying connection. Never raises."""
        with self._lock:
            try:
                self._conn.close()
            except Exception:  # close must never raise
                pass


class SQLiteAiSystemStore(_SQLiteJsonStore):
    """Durable implementation of the ``AiSystemStore`` protocol."""

    _TABLE = "ai_systems"

    async def save(self, system: AiSystem) -> None:
        await self._save(system.id, system.to_dict())

    async def get(self, system_id: str) -> AiSystem | None:
        return await self._load(system_id, AiSystem.from_dict)

    async def list_all(self) -> list[AiSystem]:
        return await self._load_all(AiSystem.from_dict)

    async def delete(self, system_id: str) -> bool:
        return await self._remove(system_id)


class SQLiteTechnicalDocumentationStore(_SQLiteJsonStore):
    """Durable store for Annex IV technical documentation."""

    _TABLE = "technical_documentation"

    async def save(self, document: TechnicalDocumentation) -> None:
        await self._save(document.id, document.to_dict())

    async def get(self, document_id: str) -> TechnicalDocumentation | None:
        return await self._load(document_id, TechnicalDocumentation.from_dict)

    async def list_all(self) -> list[TechnicalDocumentation]:
        return await self._load_all(TechnicalDocumentation.from_dict)

    async def delete(self, document_id: str) -> bool:
        return await self._remove(document_id)


class SQLiteFriaStore(_SQLiteJsonStore):
    """Durable store for Art. 27 fundamental rights impact assessments."""

    _TABLE = "fria_assessments"

    async def save(self, assessment: FundamentalRightsImpactAssessment) -> None:
        await self._save(assessment.id, assessment.to_dict())

    async def get(self, assessment_id: str) -> FundamentalRightsImpactAssessment | None:
        return await self._load(
            assessment_id, FundamentalRightsImpactAssessment.from_dict
        )

    async def list_all(self) -> list[FundamentalRightsImpactAssessment]:
        return await self._load_all(FundamentalRightsImpactAssessment.from_dict)

    async def delete(self, assessment_id: str) -> bool:
        return await self._remove(assessment_id)


class SQLiteRopaStore(_SQLiteJsonStore):
    """Durable store for the GDPR Art. 30 register of processing activities."""

    _TABLE = "processing_activities"

    async def save(self, activity: ProcessingActivity) -> None:
        await self._save(activity.id, activity.to_dict())

    async def get(self, activity_id: str) -> ProcessingActivity | None:
        return await self._load(activity_id, ProcessingActivity.from_dict)

    async def list_all(self) -> list[ProcessingActivity]:
        return await self._load_all(ProcessingActivity.from_dict)

    async def delete(self, activity_id: str) -> bool:
        return await self._remove(activity_id)


class SQLitePostMarketStore(_SQLiteJsonStore):
    """Durable store for Art. 72 post-market monitoring plans.

    Observations travel inside the plan payload, so the collected-data history
    — the evidence that monitoring was *active* — survives a restart with it.
    """

    _TABLE = "post_market_plans"

    async def save(self, plan: PostMarketMonitoringPlan) -> None:
        await self._save(plan.id, plan.to_dict())

    async def get(self, plan_id: str) -> PostMarketMonitoringPlan | None:
        return await self._load(plan_id, PostMarketMonitoringPlan.from_dict)

    async def list_all(self) -> list[PostMarketMonitoringPlan]:
        return await self._load_all(PostMarketMonitoringPlan.from_dict)

    async def delete(self, plan_id: str) -> bool:
        return await self._remove(plan_id)


class SQLiteRiskManagementStore(_SQLiteJsonStore):
    """Durable store for Art. 9 risk management files."""

    _TABLE = "risk_management"

    async def save(self, file: RiskManagementSystem) -> None:
        await self._save(file.id, file.to_dict())

    async def get(self, file_id: str) -> RiskManagementSystem | None:
        return await self._load(file_id, RiskManagementSystem.from_dict)

    async def list_all(self) -> list[RiskManagementSystem]:
        return await self._load_all(RiskManagementSystem.from_dict)

    async def delete(self, file_id: str) -> bool:
        return await self._remove(file_id)


class SQLiteInstructionsStore(_SQLiteJsonStore):
    """Durable store for Art. 13 instructions for use."""

    _TABLE = "instructions_for_use"

    async def save(self, instructions: InstructionsForUse) -> None:
        await self._save(instructions.id, instructions.to_dict())

    async def get(self, instructions_id: str) -> InstructionsForUse | None:
        return await self._load(instructions_id, InstructionsForUse.from_dict)

    async def list_all(self) -> list[InstructionsForUse]:
        return await self._load_all(InstructionsForUse.from_dict)

    async def delete(self, instructions_id: str) -> bool:
        return await self._remove(instructions_id)


class SQLiteDpiaStore(_SQLiteJsonStore):
    """Durable store for GDPR Art. 35 data protection impact assessments."""

    _TABLE = "dpia_assessments"

    async def save(self, assessment: DataProtectionImpactAssessment) -> None:
        await self._save(assessment.id, assessment.to_dict())

    async def get(self, assessment_id: str) -> DataProtectionImpactAssessment | None:
        return await self._load(assessment_id, DataProtectionImpactAssessment.from_dict)

    async def list_all(self) -> list[DataProtectionImpactAssessment]:
        return await self._load_all(DataProtectionImpactAssessment.from_dict)

    async def delete(self, assessment_id: str) -> bool:
        return await self._remove(assessment_id)


__all__ = [
    "SQLiteAiSystemStore",
    "SQLiteDpiaStore",
    "SQLiteInstructionsStore",
    "SQLiteRiskManagementStore",
    "SQLiteFriaStore",
    "SQLitePostMarketStore",
    "SQLiteRopaStore",
    "SQLiteTechnicalDocumentationStore",
]
