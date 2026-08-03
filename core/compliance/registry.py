"""AI system registry — the inventory every AI Act obligation hangs off.

Without an inventory, "are we AI Act compliant?" is unanswerable: the duties in
Art. 11, 27, 49 and 73 all attach to *a specific system in a specific risk
category*, and an organisation that cannot enumerate its systems cannot show
which duties it owes, let alone that it met them.

The registry stores :class:`~core.compliance.types.AiSystem` records, screens
declarations against Art. 5, derives the Art. 6 category, and audits every
registration and reclassification. The store is a Protocol with an in-memory
reference implementation and a durable SQLite one, matching
:mod:`core.incidents`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from core.compliance.classification import (
    ClassificationResult,
    classify_system,
    obligations_for,
)
from core.compliance.prohibited import (
    ProhibitedPractice,
    ProhibitionScreening,
    screen_practices,
)
from core.compliance.types import (
    AiSystem,
    LifecycleStage,
    RiskCategory,
    _utcnow,
)
from core.observability.audit import AuditEventType, get_audit_logger
from core.observability.logging import get_logger

logger = get_logger(__name__)


class AiSystemNotFoundError(LookupError):
    """Raised when a system id does not resolve to a registered record."""

    def __init__(self, system_id: str) -> None:
        super().__init__(f"AI system not registered: {system_id}")
        self.system_id = system_id


class AiSystemStore(Protocol):
    """Persistence boundary for the AI system registry."""

    async def save(self, system: AiSystem) -> None:
        """Insert or update a system record."""
        ...

    async def get(self, system_id: str) -> AiSystem | None:
        """Fetch a system by id, or ``None`` if unknown."""
        ...

    async def list_all(self) -> list[AiSystem]:
        """Return every registered system."""
        ...

    async def delete(self, system_id: str) -> bool:
        """Remove a system; returns whether anything was removed."""
        ...


class InMemoryAiSystemStore:
    """Reference in-memory store (non-durable; tests/single-process)."""

    def __init__(self) -> None:
        self._systems: dict[str, AiSystem] = {}

    async def save(self, system: AiSystem) -> None:
        self._systems[system.id] = system

    async def get(self, system_id: str) -> AiSystem | None:
        return self._systems.get(system_id)

    async def list_all(self) -> list[AiSystem]:
        return list(self._systems.values())

    async def delete(self, system_id: str) -> bool:
        return self._systems.pop(system_id, None) is not None


class AiSystemRegistry:
    """Registration, classification and lifecycle for AI systems."""

    def __init__(self, store: AiSystemStore | None = None) -> None:
        self._store = store or InMemoryAiSystemStore()

    @property
    def store(self) -> AiSystemStore:
        return self._store

    async def register(
        self,
        system: AiSystem,
        *,
        prohibited_practices: list[ProhibitedPractice] | None = None,
        classify: bool = True,
    ) -> tuple[AiSystem, ClassificationResult | None]:
        """Register ``system``, screening Art. 5 and deriving Art. 6.

        Returns the stored record and the classification result (``None`` when
        ``classify=False``, i.e. the operator pinned the category by hand).

        Registration never *raises* on a prohibited declaration — it records the
        ``PROHIBITED`` category and audits it. Blocking is a deployment policy
        decision; use :func:`core.compliance.prohibited.enforce_practices` at the
        call site when the deployment should refuse outright.
        """
        screening = screen_practices(system.name, prohibited_practices)
        result: ClassificationResult | None = None
        if classify:
            result = classify_system(
                system,
                prohibited_practices=(
                    screening.practices if screening.is_prohibited else None
                ),
            )
            system.risk_category = result.category
            system.classified_at = _utcnow()
        system.updated_at = _utcnow()
        await self._store.save(system)

        logger.info(
            "AUDIT | COMPLIANCE | system registered | id=%s name=%s category=%s role=%s",
            system.id,
            system.name,
            system.risk_category.value,
            system.role.value,
        )
        await get_audit_logger().log(
            AuditEventType.COMPLIANCE_REGISTER,
            resource=system.id,
            action="register",
            success=system.risk_category is not RiskCategory.PROHIBITED,
            details={
                "name": system.name,
                "version": system.version,
                "role": system.role.value,
                "risk_category": system.risk_category.value,
                "requires_registration": system.requires_registration,
                "rationale": result.rationale if result else "operator-pinned",
            },
        )
        return system, result

    async def reclassify(
        self,
        system_id: str,
        *,
        prohibited_practices: list[ProhibitedPractice] | None = None,
    ) -> tuple[AiSystem, ClassificationResult]:
        """Re-derive the risk category after the system's facts changed.

        Art. 6 classification is not a one-off: adding an Annex III use case or
        starting to profile natural persons changes the answer, and the change
        must be visible in the record.
        """
        system = await self.require(system_id)
        previous = system.risk_category
        screening = screen_practices(system.name, prohibited_practices)
        result = classify_system(
            system,
            prohibited_practices=(
                screening.practices if screening.is_prohibited else None
            ),
        )
        system.risk_category = result.category
        system.classified_at = _utcnow()
        system.updated_at = system.classified_at
        await self._store.save(system)

        logger.info(
            "AUDIT | COMPLIANCE | reclassified | id=%s %s -> %s",
            system.id,
            previous.value,
            result.category.value,
        )
        await get_audit_logger().log(
            AuditEventType.COMPLIANCE_ASSESSMENT,
            resource=system.id,
            action="reclassify",
            details={
                "previous": previous.value,
                "current": result.category.value,
                "rationale": result.rationale,
                "citations": result.citations,
            },
        )
        return system, result

    async def advance_lifecycle(
        self, system_id: str, stage: LifecycleStage, *, at: datetime | None = None
    ) -> AiSystem:
        """Move a system to a new lifecycle stage and stamp the relevant date."""
        system = await self.require(system_id)
        stamp = at or _utcnow()
        system.lifecycle_stage = stage
        if stage is LifecycleStage.PLACED_ON_MARKET:
            system.placed_on_market_at = stamp
        elif stage is LifecycleStage.WITHDRAWN:
            system.withdrawn_at = stamp
        system.updated_at = stamp
        await self._store.save(system)
        logger.info(
            "AUDIT | COMPLIANCE | lifecycle | id=%s stage=%s", system.id, stage.value
        )
        await get_audit_logger().log(
            AuditEventType.COMPLIANCE_REGISTER,
            resource=system.id,
            action="lifecycle",
            details={"stage": stage.value, "at": stamp.isoformat()},
        )
        return system

    async def get(self, system_id: str) -> AiSystem | None:
        """Fetch a registered system by id."""
        return await self._store.get(system_id)

    async def require(self, system_id: str) -> AiSystem:
        """Fetch a system, raising :class:`AiSystemNotFoundError` if unknown."""
        system = await self._store.get(system_id)
        if system is None:
            raise AiSystemNotFoundError(system_id)
        return system

    async def list_systems(
        self,
        *,
        risk_category: RiskCategory | None = None,
        lifecycle_stage: LifecycleStage | None = None,
    ) -> list[AiSystem]:
        """List registered systems, optionally filtered."""
        systems = await self._store.list_all()
        if risk_category is not None:
            systems = [s for s in systems if s.risk_category is risk_category]
        if lifecycle_stage is not None:
            systems = [s for s in systems if s.lifecycle_stage is lifecycle_stage]
        return systems

    async def high_risk_systems(self) -> list[AiSystem]:
        """Every system carrying the Chapter III obligation set."""
        return await self.list_systems(risk_category=RiskCategory.HIGH_RISK)

    async def unregistered_with_authority(self) -> list[AiSystem]:
        """Systems owing an Art. 49 EU-database registration that has not happened.

        Withdrawn systems are excluded — the duty attaches to placing on the
        market, not to a record that was never shipped.
        """
        pending: list[AiSystem] = []
        for system in await self._store.list_all():
            if system.lifecycle_stage is LifecycleStage.WITHDRAWN:
                continue
            if (
                system.requires_registration
                and system.conformity.eu_database_registration_at is None
            ):
                pending.append(system)
        return pending

    async def obligations(self, system_id: str) -> list[str]:
        """Headline obligations attaching to a registered system."""
        system = await self.require(system_id)
        return obligations_for(system.risk_category)

    async def screen(
        self, system_id: str, practices: list[ProhibitedPractice]
    ) -> ProhibitionScreening:
        """Re-screen a registered system's declared practices against Art. 5."""
        system = await self.require(system_id)
        return screen_practices(system.name, practices)

    async def summary(self) -> dict[str, Any]:
        """Inventory roll-up: counts by category and the open Art. 49 duties."""
        systems = await self._store.list_all()
        by_category: dict[str, int] = {}
        for system in systems:
            key = system.risk_category.value
            by_category[key] = by_category.get(key, 0) + 1
        pending = await self.unregistered_with_authority()
        return {
            "total": len(systems),
            "by_category": by_category,
            "pending_eu_registration": [s.id for s in pending],
        }


_registry: AiSystemRegistry | None = None


def _build_registry() -> AiSystemRegistry:
    """Build the registry, selecting a durable store iff a DB path is set."""
    from core.config.compliance import get_compliance_config

    path = get_compliance_config().registry_db_path
    if path:
        from core.compliance.persistence import SQLiteAiSystemStore

        return AiSystemRegistry(store=SQLiteAiSystemStore(path))
    return AiSystemRegistry()


def get_ai_system_registry() -> AiSystemRegistry:
    """Get or create the global AI system registry."""
    global _registry
    if _registry is None:
        _registry = _build_registry()
    return _registry


def reset_ai_system_registry() -> None:
    """Drop the cached registry (tests, and reconfiguration)."""
    global _registry
    _registry = None


__all__ = [
    "AiSystemNotFoundError",
    "AiSystemRegistry",
    "AiSystemStore",
    "InMemoryAiSystemStore",
    "get_ai_system_registry",
    "reset_ai_system_registry",
]
