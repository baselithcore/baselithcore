"""Post-market monitoring service and review sweep (AI Act Art. 72).

:mod:`core.compliance.post_market` models the plan; this module keeps it alive.
Art. 72(1) requires the monitoring system to be **active** — a plan drawn up at
launch, stored in a variable, and never revisited satisfies nothing. So the
service adds the two properties a plan needs to count as monitoring:

* **durability** — plans and their observations survive restarts, because the
  observation history *is* the evidence that data was actively collected;
* **a heartbeat** — a background sweep surfaces plans past their review cadence
  and breaches that nobody acknowledged, so an unreviewed plan becomes visible
  instead of silently ageing.

Every write is audited. Acting on a breach — opening the Art. 73 serious
incident question, freezing a rollout — remains the operator's decision.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Protocol

from core.compliance.post_market import PostMarketMonitoringPlan, PostMarketObservation
from core.compliance.types import _utcnow
from core.observability.audit import AuditEventType, get_audit_logger
from core.observability.logging import get_logger

logger = get_logger(__name__)

# A daily sweep bounds how long an overdue review or an unnoticed breach can go
# unreported to ~24h, at negligible cost.
_SWEEP_INTERVAL_SECONDS = 24 * 3600


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

    async def review(self, plan_id: str, *, at: datetime | None = None) -> PostMarketMonitoringPlan:
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


class PostMarketReviewScheduler:
    """Owns the periodic Art. 72 review sweep and its lifecycle."""

    def __init__(self, interval_seconds: int = _SWEEP_INTERVAL_SECONDS) -> None:
        self._interval = interval_seconds
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Schedule the sweep loop. Idempotent — a second call is a no-op."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="post-market-review-sweep")
        logger.info(
            "post_market_review_scheduler_started",
            extra={"interval_seconds": self._interval},
        )

    async def stop(self) -> None:
        """Cancel the sweep loop and await its teardown. Idempotent."""
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await self.sweep()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "post_market_review_sweep_failed", extra={"error": str(exc)}
                )
            await asyncio.sleep(self._interval)

    async def sweep(self) -> dict[str, list[str]]:
        """Report overdue reviews and incomplete plans once. Never raises."""
        service = get_post_market_service()
        overdue = await service.overdue_reviews()
        incomplete = await service.incomplete()
        for plan in overdue:
            logger.warning(
                "AUDIT | COMPLIANCE | post-market review overdue | system=%s plan=%s "
                "(Art. 72(1) requires the monitoring system to stay active)",
                plan.system_id,
                plan.id,
            )
        if overdue or incomplete:
            await get_audit_logger().log(
                AuditEventType.COMPLIANCE_ASSESSMENT,
                action="post_market_sweep",
                success=not overdue,
                details={
                    "overdue_reviews": [p.id for p in overdue],
                    "incomplete_plans": [p.id for p in incomplete],
                },
            )
        return {
            "overdue_reviews": [p.id for p in overdue],
            "incomplete_plans": [p.id for p in incomplete],
        }


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
    "PostMarketReviewScheduler",
    "PostMarketService",
    "PostMarketStore",
    "get_post_market_service",
    "reset_post_market_service",
]
