"""The SOC 2 service: scopes, controls, evidence, collection and readiness.

Every change writes to the audit trail, and every piece of evidence writes its
digest there too. That is the point of recording evidence *here* rather than in
a folder: the trail is hash-chained, so an artefact whose hash was anchored
when it was recorded cannot be swapped for a better one before the audit
without the swap being visible.

The service reports and records. It never marks a control effective, and it
cannot produce the attestation: that is the auditor's opinion.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from baselith.core.compliance.soc2.catalogue import is_known
from baselith.core.compliance.soc2.collectors import (
    EvidenceDraft,
    registered_collectors,
)
from baselith.core.compliance.soc2.persistence import InMemorySoc2Store, Soc2Store
from baselith.core.compliance.soc2.readiness import (
    ControlReadiness,
    CriterionReadiness,
    assess_scope,
    summarise,
)
from baselith.core.compliance.soc2.starter import starter_controls
from baselith.core.compliance.soc2.types import (
    AttestationType,
    ControlFrequency,
    EvidenceKind,
    EvidenceOutcome,
    Soc2Control,
    Soc2Evidence,
    Soc2Scope,
    TrustCategory,
)
from baselith.core.incidents.types import require_aware
from baselith.core.observability.audit import AuditEventType, get_audit_logger
from baselith.core.observability.logging import get_logger

logger = get_logger(__name__)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class Soc2NotFoundError(LookupError):
    """No scope, control or evidence with that id."""


class Soc2ValidationError(ValueError):
    """The request names an unknown criterion or an impossible period."""


def canonical_sha256(payload: Any) -> str:
    """SHA-256 of ``payload`` as canonical JSON (sorted keys, no whitespace)."""
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass
class CollectionReport:
    """What one collection run did."""

    collected: int = 0
    exceptions: int = 0
    #: Collector name → how it went (``ok``, ``exception`` or ``failed``).
    collectors: dict[str, str] = field(default_factory=dict)
    #: Collector names a control lists that are not registered.
    unknown: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "collected": self.collected,
            "exceptions": self.exceptions,
            "collectors": dict(self.collectors),
            "unknown": list(self.unknown),
        }


class Soc2Service:
    """Scopes, controls and evidence over a :class:`Soc2Store`."""

    def __init__(self, store: Soc2Store | None = None) -> None:
        self._store: Soc2Store = store or InMemorySoc2Store()

    @property
    def store(self) -> Soc2Store:
        return self._store

    # -- scopes ----------------------------------------------------------

    async def create_scope(
        self,
        name: str,
        *,
        categories: list[TrustCategory] | None = None,
        attestation_type: AttestationType = AttestationType.TYPE_I,
        period_start: date | None = None,
        period_end: date | None = None,
        system_description: str = "",
    ) -> Soc2Scope:
        scope = Soc2Scope(
            name=name,
            categories=list(categories or [TrustCategory.SECURITY]),
            attestation_type=attestation_type,
            system_description=system_description,
        )
        if period_start is not None:
            scope.period_start = period_start
        scope.period_end = period_end
        if attestation_type is AttestationType.TYPE_I and period_end is None:
            # A design is examined as of one date: that date is the as-of date.
            scope.period_end = scope.period_start
        self._check_period(scope)
        await self._store.save_scope(scope)
        await self._audit(
            AuditEventType.COMPLIANCE_REGISTER,
            scope.id,
            "soc2_scope",
            {"name": name, "type": scope.attestation_type.value},
        )
        return scope

    async def update_scope(self, scope_id: str, **fields: Any) -> Soc2Scope:
        scope = await self.require_scope(scope_id)
        for key in (
            "name",
            "categories",
            "attestation_type",
            "period_start",
            "period_end",
            "system_description",
        ):
            if key in fields and fields[key] is not None:
                setattr(scope, key, fields[key])
        if TrustCategory.SECURITY not in scope.categories:
            scope.categories = [TrustCategory.SECURITY, *scope.categories]
        self._check_period(scope)
        scope.updated_at = datetime.now(UTC)
        await self._store.save_scope(scope)
        await self._audit(
            AuditEventType.COMPLIANCE_REGISTER,
            scope.id,
            "soc2_scope_update",
            {"fields": sorted(k for k in fields if fields[k] is not None)},
        )
        return scope

    @staticmethod
    def _check_period(scope: Soc2Scope) -> None:
        if scope.period_end is not None and scope.period_end < scope.period_start:
            raise Soc2ValidationError("the period ends before it starts")

    async def require_scope(self, scope_id: str) -> Soc2Scope:
        scope = await self._store.get_scope(scope_id)
        if scope is None:
            raise Soc2NotFoundError(scope_id)
        return scope

    async def list_scopes(self) -> list[Soc2Scope]:
        return await self._store.list_scopes()

    # -- controls --------------------------------------------------------

    def _check_criteria(self, criteria: list[str]) -> None:
        unknown = [c for c in criteria if not is_known(c)]
        if unknown:
            raise Soc2ValidationError(f"unknown criteria: {', '.join(unknown)}")

    async def add_control(
        self,
        title: str,
        *,
        description: str = "",
        owner: str = "",
        criteria: list[str] | None = None,
        frequency: ControlFrequency = ControlFrequency.MONTHLY,
        collector_names: list[str] | None = None,
    ) -> Soc2Control:
        self._check_criteria(criteria or [])
        control = Soc2Control(
            title=title,
            description=description,
            owner=owner,
            criteria=list(criteria or []),
            frequency=frequency,
            collectors=list(collector_names or []),
        )
        await self._store.save_control(control)
        await self._audit(
            AuditEventType.COMPLIANCE_REGISTER,
            control.id,
            "soc2_control",
            {"title": title, "criteria": control.criteria},
        )
        return control

    async def update_control(self, control_id: str, **fields: Any) -> Soc2Control:
        control = await self.require_control(control_id)
        if fields.get("criteria") is not None:
            self._check_criteria(fields["criteria"])
        for key, attr in (
            ("title", "title"),
            ("description", "description"),
            ("owner", "owner"),
            ("criteria", "criteria"),
            ("frequency", "frequency"),
            ("collector_names", "collectors"),
            ("active", "active"),
        ):
            if key in fields and fields[key] is not None:
                setattr(control, attr, fields[key])
        control.updated_at = datetime.now(UTC)
        await self._store.save_control(control)
        await self._audit(
            AuditEventType.COMPLIANCE_REGISTER,
            control.id,
            "soc2_control_update",
            {"fields": sorted(k for k in fields if fields[k] is not None)},
        )
        return control

    async def seed_starter_controls(self, owner: str) -> list[Soc2Control]:
        """Add the starter controls that are not already present (by title)."""
        existing = {c.title for c in await self._store.list_controls()}
        added: list[Soc2Control] = []
        for control in starter_controls(owner):
            if control.title in existing:
                continue
            await self._store.save_control(control)
            await self._audit(
                AuditEventType.COMPLIANCE_REGISTER,
                control.id,
                "soc2_control",
                {"title": control.title, "criteria": control.criteria, "starter": True},
            )
            added.append(control)
        return added

    async def require_control(self, control_id: str) -> Soc2Control:
        control = await self._store.get_control(control_id)
        if control is None:
            raise Soc2NotFoundError(control_id)
        return control

    async def list_controls(self) -> list[Soc2Control]:
        return await self._store.list_controls()

    # -- evidence --------------------------------------------------------

    async def record_evidence(
        self,
        control_id: str,
        title: str,
        *,
        summary: str = "",
        outcome: EvidenceOutcome = EvidenceOutcome.OPERATED,
        reference: str = "",
        recorded_by: str = "",
        content: str | None = None,
        content_sha256: str | None = None,
        collected_at: datetime | None = None,
    ) -> Soc2Evidence:
        """Record manual evidence for a control.

        The digest binds the record to what it points at: ``content_sha256``
        when the operator hashed the artefact themselves, else the hash of
        ``content``, else of the record's own title, summary and reference —
        which fixes what was *said*, not the document behind a link.
        """
        await self.require_control(control_id)
        if collected_at is not None:
            try:
                require_aware(collected_at, "collected_at")
            except ValueError as exc:
                raise Soc2ValidationError(str(exc)) from exc
        if content_sha256 is not None:
            digest = content_sha256.strip().lower()
            if not _SHA256.match(digest):
                raise Soc2ValidationError("content_sha256 must be 64 hex characters")
        elif content is not None:
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        else:
            digest = canonical_sha256(
                {"title": title, "summary": summary, "reference": reference}
            )
        return await self._store_evidence(
            Soc2Evidence(
                control_id=control_id,
                title=title,
                kind=EvidenceKind.MANUAL,
                source="manual",
                sha256=digest,
                outcome=outcome,
                summary=summary,
                reference=reference,
                recorded_by=recorded_by,
                collected_at=collected_at or datetime.now(UTC),
            )
        )

    async def _store_evidence(self, evidence: Soc2Evidence) -> Soc2Evidence:
        await self._store.save_evidence(evidence)
        await self._audit(
            AuditEventType.COMPLIANCE_ASSESSMENT,
            evidence.id,
            "soc2_evidence",
            {
                "control_id": evidence.control_id,
                "sha256": evidence.sha256,
                "source": evidence.source,
                "outcome": evidence.outcome.value,
                "collected_at": evidence.collected_at.isoformat(),
            },
            success=evidence.outcome is not EvidenceOutcome.EXCEPTION,
        )
        return evidence

    async def list_evidence(self, control_id: str | None = None) -> list[Soc2Evidence]:
        return await self._store.list_evidence(control_id)

    # -- collection ------------------------------------------------------

    async def collect(self, recorded_by: str = "") -> CollectionReport:
        """Run every collector an active control lists and attach the results.

        A collector that raises is recorded as an exception on the controls
        that rely on it, never skipped: a control whose evidence silently
        stopped arriving would read as merely stale.
        """
        report = CollectionReport()
        controls = [c for c in await self._store.list_controls() if c.active]
        wanted = sorted({n for c in controls for n in c.collectors})
        registry = registered_collectors()
        for name in wanted:
            spec = registry.get(name)
            if spec is None:
                report.unknown.append(name)
                continue
            try:
                draft = await spec.run()
                report.collectors[name] = (
                    "exception" if draft.outcome is EvidenceOutcome.EXCEPTION else "ok"
                )
            except Exception as exc:
                logger.warning("soc2_collector_failed", collector=name, exc_info=True)
                draft = EvidenceDraft(
                    f"Collector '{name}' could not run",
                    f"{type(exc).__name__}: {exc}",
                    EvidenceOutcome.EXCEPTION,
                    {"error": type(exc).__name__},
                )
                report.collectors[name] = "failed"
            # Hashed in the shape the store gives back (JSON types only), so
            # the digest can be recomputed from the stored record later.
            details = json.loads(json.dumps(draft.details, default=str))
            digest = canonical_sha256(details)
            for control in (c for c in controls if name in c.collectors):
                await self._store_evidence(
                    Soc2Evidence(
                        control_id=control.id,
                        title=draft.title,
                        kind=EvidenceKind.AUTOMATIC,
                        source=name,
                        sha256=digest,
                        outcome=draft.outcome,
                        summary=draft.summary,
                        recorded_by=recorded_by,
                        details=details,
                    )
                )
                report.collected += 1
                report.exceptions += draft.outcome is EvidenceOutcome.EXCEPTION
        return report

    # -- readiness -------------------------------------------------------

    async def readiness(
        self, scope_id: str, now: datetime | None = None
    ) -> tuple[Soc2Scope, list[CriterionReadiness], list[ControlReadiness], dict]:
        """Where ``scope_id`` stands at ``now``: criteria, controls, summary."""
        scope = await self.require_scope(scope_id)
        return await self.assess(
            scope,
            await self._store.list_controls(),
            await self._store.list_evidence(),
            now or datetime.now(UTC),
        )

    @staticmethod
    async def assess(
        scope: Soc2Scope,
        controls: list[Soc2Control],
        evidence: list[Soc2Evidence],
        now: datetime,
    ) -> tuple[Soc2Scope, list[CriterionReadiness], list[ControlReadiness], dict]:
        """:meth:`readiness` over records the caller already holds.

        The assessment is pure CPU over the whole evidence set, so it runs on a
        worker thread: on the event loop a large engagement stalled every other
        request for as long as it took.
        """
        criteria, assessed = await asyncio.to_thread(
            assess_scope, scope, controls, evidence, now
        )
        return scope, criteria, assessed, summarise(criteria)

    # -- audit -----------------------------------------------------------

    @staticmethod
    async def _audit(
        event: AuditEventType,
        resource: str,
        action: str,
        details: dict[str, Any],
        *,
        success: bool = True,
    ) -> None:
        logger.info("AUDIT | SOC2 | %s | resource=%s", action, resource)
        await get_audit_logger().log(
            event,
            resource=resource,
            action=action,
            success=success,
            details={"regime": "soc2", **details},
        )


_service: Soc2Service | None = None


def get_soc2_service() -> Soc2Service:
    """Get or create the global service (durable when ``COMPLIANCE_SOC2_DB_PATH`` is set)."""
    global _service
    if _service is None:
        from baselith.core.config.compliance import get_compliance_config

        path = get_compliance_config().soc2_db_path
        store: Soc2Store | None = None
        if path:
            from baselith.core.compliance.soc2.persistence import SQLiteSoc2Store

            store = SQLiteSoc2Store(path)
        _service = Soc2Service(store)
    return _service


def reset_soc2_service() -> None:
    """Drop the cached service (tests, reconfiguration)."""
    global _service
    _service = None


__all__ = [
    "CollectionReport",
    "Soc2NotFoundError",
    "Soc2Service",
    "Soc2ValidationError",
    "canonical_sha256",
    "get_soc2_service",
    "reset_soc2_service",
]
