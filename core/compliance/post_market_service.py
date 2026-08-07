"""Post-market monitoring service and review sweep (AI Act Art. 72).

:mod:`core.compliance.post_market` models the plan; this module keeps it alive.
Art. 72(1) requires the monitoring system to be **active** — a plan drawn up at
launch, stored in a variable, and never revisited satisfies nothing. So the
service adds the two properties a plan needs to count as monitoring:

* **durability** — plans and their observations survive restarts, because the
  observation history *is* the evidence that data was actively collected;
* **a heartbeat** — :class:`~core.compliance.review_sweep.ComplianceReviewScheduler`
  polls :meth:`PostMarketService.overdue_reviews` daily, so an unreviewed plan
  becomes visible instead of silently ageing.

Every write is audited. Acting on a breach — opening the Art. 73 serious
incident question, freezing a rollout — remains the operator's decision.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from core.compliance.post_market import PostMarketMonitoringPlan, PostMarketObservation
from core.compliance.types import _utcnow
from core.observability.audit import AuditEventType, get_audit_logger
from core.observability.logging import get_logger

logger = get_logger(__name__)


class PostMarketStore(Protocol):
    """Persistence boundary for monitoring plans."""

    async def save(self, plan: PostMarketMonitoringPlan) -> None:
        """Insert or update a plan (observations travel with it)."""
        ...

    async def get(self, plan_id: str) -> PostMarketMonitoringPlan | None:
        """Fetch a plan by id, or ``None`` if unknown."""
        ...

    async def list_all(self) -> list[PostMarketMonitoringPlan]:
        """Return every stored plan."""
        ...

    async def delete(self, plan_id: str) -> bool:
        """Remove a plan; returns whether anything was removed."""
        ...


class InMemoryPostMarketStore:
    """Reference in-memory store (non-durable; tests/single-process)."""

    def __init__(self) -> None:
        self._plans: dict[str, PostMarketMonitoringPlan] = {}

    async def save(self, plan: PostMarketMonitoringPlan) -> None:
        self._plans[plan.id] = plan

    async def get(self, plan_id: str) -> PostMarketMonitoringPlan | None:
        return self._plans.get(plan_id)

    async def list_all(self) -> list[PostMarketMonitoringPlan]:
        return list(self._plans.values())

    async def delete(self, plan_id: str) -> bool:
        return self._plans.pop(plan_id, None) is not None


class PostMarketService:
    """Store plans, record observations, and surface what needs attention."""

    def __init__(self, store: PostMarketStore | None = None) -> None:
        self._store = store or InMemoryPostMarketStore()

    async def save(self, plan: PostMarketMonitoringPlan) -> PostMarketMonitoringPlan:
        """Store a plan and audit its completeness."""
        plan.updated_at = _utcnow()
        await self._store.save(plan)
        await get_audit_logger().log(
            AuditEventType.COMPLIANCE_ASSESSMENT,
            resource=plan.system_id,
            action="post_market_plan",
            success=plan.is_complete,
            details={
                "plan_id": plan.id,
                "metrics": [m.name for m in plan.metrics],
                "missing_elements": plan.missing_elements(),
            },
        )
        return plan

    async def observe(
        self,
        plan_id: str,
        metric: str,
        value: float,
        *,
        at: datetime | None = None,
        context: dict[str, Any] | None = None,
    ) -> PostMarketObservation:
        """Record a production measurement against a plan and persist it.

        A breach is audited as a **failed** event, so it stands out in the trail
        rather than blending into routine telemetry.
        """
        plan = await self.require(plan_id)
        observation = plan.observe(metric, value, at=at, context=context)
        await self._store.save(plan)
        if observation.is_breach:
            logger.warning(
                "AUDIT | COMPLIANCE | post-market threshold breach | system=%s "
                "metric=%s value=%s",
                plan.system_id,
                metric,
                value,
            )
        await get_audit_logger().log(
            AuditEventType.COMPLIANCE_ASSESSMENT,
            resource=plan.system_id,
            action="post_market_observation",
            success=not observation.is_breach,
            details={
                "plan_id": plan.id,
                "metric": metric,
                "value": value,
                "breach": observation.is_breach,
            },
        )
        return observation

    async def review(
        self, plan_id: str, *, at: datetime | None = None
    ) -> PostMarketMonitoringPlan:
        """Record a plan review, resetting the Art. 72(1) cadence."""
        plan = await self.require(plan_id)
        plan.last_reviewed_at = at or _utcnow()
        return await self.save(plan)

    async def get(self, plan_id: str) -> PostMarketMonitoringPlan | None:
        return await self._store.get(plan_id)

    async def require(self, plan_id: str) -> PostMarketMonitoringPlan:
        plan = await self._store.get(plan_id)
        if plan is None:
            raise LookupError(f"Post-market monitoring plan not found: {plan_id}")
        return plan

    async def for_system(self, system_id: str) -> list[PostMarketMonitoringPlan]:
        """Every plan recorded for one AI system."""
        return [p for p in await self._store.list_all() if p.system_id == system_id]

    async def list_plans(self) -> list[PostMarketMonitoringPlan]:
        return await self._store.list_all()

    async def incomplete(self) -> list[PostMarketMonitoringPlan]:
        """Plans with at least one empty Art. 72 element."""
        return [p for p in await self._store.list_all() if not p.is_complete]

    async def overdue_reviews(
        self, now: datetime | None = None
    ) -> list[PostMarketMonitoringPlan]:
        """Plans past their review cadence — including never-reviewed ones."""
        return [p for p in await self._store.list_all() if p.is_review_overdue(now)]

    async def open_breaches(
        self, *, since: datetime | None = None
    ) -> list[tuple[PostMarketMonitoringPlan, PostMarketObservation]]:
        """``(plan, observation)`` pairs where a threshold was breached."""
        found: list[tuple[PostMarketMonitoringPlan, PostMarketObservation]] = []
        for plan in await self._store.list_all():
            found.extend((plan, o) for o in plan.breaches(since=since))
        return found


_service: PostMarketService | None = None


def get_post_market_service() -> PostMarketService:
    """Get or create the global post-market monitoring service."""
    global _service
    if _service is None:
        from core.config.compliance import get_compliance_config

        path = get_compliance_config().post_market_db_path
        if path:
            from core.compliance.persistence import SQLitePostMarketStore

            _service = PostMarketService(store=SQLitePostMarketStore(path))
        else:
            _service = PostMarketService()
    return _service


def reset_post_market_service() -> None:
    """Drop the cached service (tests, and reconfiguration)."""
    global _service
    _service = None


__all__ = [
    "InMemoryPostMarketStore",
    "PostMarketService",
    "PostMarketStore",
    "get_post_market_service",
    "reset_post_market_service",
]
