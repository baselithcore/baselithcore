"""Stores for scopes, controls and evidence: in memory, or one durable SQLite file.

Same rationale as the other compliance stores (see
:mod:`baselith.core.compliance.persistence`): stdlib ``sqlite3``, one JSON blob
per record, a Protocol boundary a Postgres implementation can take over. All
three kinds live in one file, one table each, so a single path
(``COMPLIANCE_SOC2_DB_PATH``) makes the whole engagement durable — and it must
be durable: evidence recorded over a Type II period is worth exactly as much as
its survival to the audit.
"""

from __future__ import annotations

from typing import Protocol

from baselith.core.compliance.persistence import RecordExistsError, _SQLiteJsonStore
from baselith.core.compliance.soc2.types import Soc2Control, Soc2Evidence, Soc2Scope


class Soc2Store(Protocol):
    """Everything the service persists."""

    async def save_scope(self, scope: Soc2Scope) -> None: ...
    async def get_scope(self, scope_id: str) -> Soc2Scope | None: ...
    async def list_scopes(self) -> list[Soc2Scope]: ...

    async def save_control(self, control: Soc2Control) -> None: ...
    async def get_control(self, control_id: str) -> Soc2Control | None: ...
    async def list_controls(self) -> list[Soc2Control]: ...

    async def save_evidence(self, evidence: Soc2Evidence) -> None:
        """Add one piece of evidence. Evidence is written once: a second save
        under the same id raises :class:`RecordExistsError` instead of
        replacing what an auditor may already have been shown."""
        ...

    async def get_evidence(self, evidence_id: str) -> Soc2Evidence | None: ...
    async def list_evidence(
        self, control_id: str | None = None
    ) -> list[Soc2Evidence]: ...


class InMemorySoc2Store:
    """Reference store (non-durable; tests and single-process development)."""

    def __init__(self) -> None:
        self._scopes: dict[str, Soc2Scope] = {}
        self._controls: dict[str, Soc2Control] = {}
        self._evidence: dict[str, Soc2Evidence] = {}

    async def save_scope(self, scope: Soc2Scope) -> None:
        self._scopes[scope.id] = scope

    async def get_scope(self, scope_id: str) -> Soc2Scope | None:
        return self._scopes.get(scope_id)

    async def list_scopes(self) -> list[Soc2Scope]:
        return sorted(self._scopes.values(), key=lambda s: s.created_at)

    async def save_control(self, control: Soc2Control) -> None:
        self._controls[control.id] = control

    async def get_control(self, control_id: str) -> Soc2Control | None:
        return self._controls.get(control_id)

    async def list_controls(self) -> list[Soc2Control]:
        return sorted(self._controls.values(), key=lambda c: c.created_at)

    async def save_evidence(self, evidence: Soc2Evidence) -> None:
        if evidence.id in self._evidence:
            raise RecordExistsError(f"record already exists: {evidence.id}")
        self._evidence[evidence.id] = evidence

    async def get_evidence(self, evidence_id: str) -> Soc2Evidence | None:
        return self._evidence.get(evidence_id)

    async def list_evidence(self, control_id: str | None = None) -> list[Soc2Evidence]:
        rows = [
            e
            for e in self._evidence.values()
            if control_id is None or e.control_id == control_id
        ]
        return sorted(rows, key=lambda e: e.collected_at)


class _Table(_SQLiteJsonStore):
    """One table of a shared file; each kind gets its own connection."""


class _ScopeTable(_Table):
    _TABLE = "soc2_scopes"


class _ControlTable(_Table):
    _TABLE = "soc2_controls"


class _EvidenceTable(_Table):
    _TABLE = "soc2_evidence"


class SQLiteSoc2Store:
    """Durable store: three tables in one SQLite file."""

    def __init__(self, path: str) -> None:
        self._scopes = _ScopeTable(path)
        self._controls = _ControlTable(path)
        self._evidence = _EvidenceTable(path)

    async def save_scope(self, scope: Soc2Scope) -> None:
        await self._scopes._save(scope.id, scope.to_dict())

    async def get_scope(self, scope_id: str) -> Soc2Scope | None:
        return await self._scopes._load(scope_id, Soc2Scope.from_dict)

    async def list_scopes(self) -> list[Soc2Scope]:
        rows = await self._scopes._load_all(Soc2Scope.from_dict)
        return sorted(rows, key=lambda s: s.created_at)

    async def save_control(self, control: Soc2Control) -> None:
        await self._controls._save(control.id, control.to_dict())

    async def get_control(self, control_id: str) -> Soc2Control | None:
        return await self._controls._load(control_id, Soc2Control.from_dict)

    async def list_controls(self) -> list[Soc2Control]:
        rows = await self._controls._load_all(Soc2Control.from_dict)
        return sorted(rows, key=lambda c: c.created_at)

    async def save_evidence(self, evidence: Soc2Evidence) -> None:
        await self._evidence._add(evidence.id, evidence.to_dict())

    async def get_evidence(self, evidence_id: str) -> Soc2Evidence | None:
        return await self._evidence._load(evidence_id, Soc2Evidence.from_dict)

    async def list_evidence(self, control_id: str | None = None) -> list[Soc2Evidence]:
        rows = await self._evidence._load_all(Soc2Evidence.from_dict)
        if control_id is not None:
            rows = [e for e in rows if e.control_id == control_id]
        return sorted(rows, key=lambda e: e.collected_at)

    def close(self) -> None:
        for table in (self._scopes, self._controls, self._evidence):
            table.close()


__all__ = ["InMemorySoc2Store", "RecordExistsError", "SQLiteSoc2Store", "Soc2Store"]
