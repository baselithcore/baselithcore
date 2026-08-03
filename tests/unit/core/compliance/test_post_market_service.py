"""Tests for the Art. 72 post-market monitoring service and review sweep."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.compliance.persistence import SQLitePostMarketStore
from core.compliance.post_market import (
    MonitoringMetric,
    PostMarketMonitoringPlan,
    ThresholdDirection,
)
from core.compliance.post_market_service import (
    PostMarketReviewScheduler,
    PostMarketService,
    get_post_market_service,
    reset_post_market_service,
)


def _plan(system_id: str = "sys-1") -> PostMarketMonitoringPlan:
    return PostMarketMonitoringPlan(
        system_id=system_id,
        objectives="Detect accuracy drift.",
        metrics=[
            MonitoringMetric(
                name="accuracy", threshold=0.9, direction=ThresholdDirection.LOWER_BOUND
            ),
            MonitoringMetric(name="volume"),
        ],
        data_sources=["inference logs"],
        corrective_action_process="Freeze rollout.",
        responsible_contacts=["ml-ops@example.test"],
    )


@pytest.fixture
def service():
    return PostMarketService()


class TestPersistence:
    async def test_a_saved_plan_can_be_read_back(self, service):
        plan = await service.save(_plan())
        assert await service.get(plan.id) is not None
        assert await service.for_system("sys-1") == [plan]

    async def test_observations_survive_a_reopen(self, tmp_path):
        store = SQLitePostMarketStore(tmp_path / "pm.db")
        svc = PostMarketService(store=store)
        plan = await svc.save(_plan())
        await svc.observe(plan.id, "accuracy", 0.85)
        store.close()

        reopened = SQLitePostMarketStore(tmp_path / "pm.db")
        try:
            restored = await reopened.get(plan.id)
            assert restored is not None
            # The observation history IS the Art. 72 evidence — it must persist.
            assert len(restored.observations) == 1
            assert restored.observations[0].is_breach is True
        finally:
            reopened.close()

    async def test_delete_removes_the_plan(self, tmp_path):
        store = SQLitePostMarketStore(tmp_path / "pm.db")
        try:
            svc = PostMarketService(store=store)
            plan = await svc.save(_plan())
            assert await store.delete(plan.id) is True
            assert await svc.get(plan.id) is None
        finally:
            store.close()


class TestObservations:
    async def test_breach_is_recorded_and_flagged(self, service):
        plan = await service.save(_plan())
        observation = await service.observe(plan.id, "accuracy", 0.5)
        assert observation.is_breach is True
        breaches = await service.open_breaches()
        assert len(breaches) == 1
        assert breaches[0][0].id == plan.id

    async def test_within_threshold_is_not_a_breach(self, service):
        plan = await service.save(_plan())
        assert (await service.observe(plan.id, "accuracy", 0.95)).is_breach is False
        assert await service.open_breaches() == []

    async def test_undeclared_metric_raises(self, service):
        plan = await service.save(_plan())
        with pytest.raises(KeyError):
            await service.observe(plan.id, "unknown", 1.0)

    async def test_unknown_plan_raises(self, service):
        with pytest.raises(LookupError):
            await service.observe("nope", "accuracy", 1.0)

    async def test_breaches_can_be_filtered_by_time(self, service):
        plan = await service.save(_plan())
        await service.observe(
            plan.id, "accuracy", 0.1, at=datetime.now(UTC) - timedelta(days=10)
        )
        await service.observe(plan.id, "accuracy", 0.1)
        recent = await service.open_breaches(since=datetime.now(UTC) - timedelta(days=1))
        assert len(recent) == 1


class TestReviewCadence:
    async def test_a_stale_plan_is_reported_overdue(self, service):
        plan = _plan()
        plan.created_at = datetime.now(UTC) - timedelta(days=400)
        await service.save(plan)
        overdue = await service.overdue_reviews()
        assert [p.id for p in overdue] == [plan.id]

    async def test_a_review_clears_the_overdue_state(self, service):
        plan = _plan()
        plan.created_at = datetime.now(UTC) - timedelta(days=400)
        await service.save(plan)
        await service.review(plan.id)
        assert await service.overdue_reviews() == []

    async def test_incomplete_plans_are_listed(self, service):
        await service.save(PostMarketMonitoringPlan(system_id="sys-2"))
        incomplete = await service.incomplete()
        assert len(incomplete) == 1
        assert any("Art. 72" in m for m in incomplete[0].missing_elements())


class TestSweep:
    async def test_sweep_reports_overdue_and_incomplete(self):
        reset_post_market_service()
        try:
            service = get_post_market_service()
            stale = _plan("sys-stale")
            stale.created_at = datetime.now(UTC) - timedelta(days=400)
            await service.save(stale)
            await service.save(PostMarketMonitoringPlan(system_id="sys-empty"))

            result = await PostMarketReviewScheduler().sweep()
            assert stale.id in result["overdue_reviews"]
            assert len(result["incomplete_plans"]) == 1
        finally:
            reset_post_market_service()

    async def test_sweep_is_quiet_when_everything_is_current(self):
        reset_post_market_service()
        try:
            service = get_post_market_service()
            plan = _plan()
            plan.last_reviewed_at = datetime.now(UTC)
            await service.save(plan)
            result = await PostMarketReviewScheduler().sweep()
            assert result == {"overdue_reviews": [], "incomplete_plans": []}
        finally:
            reset_post_market_service()

    async def test_scheduler_start_is_idempotent_and_stops_cleanly(self):
        scheduler = PostMarketReviewScheduler(interval_seconds=3600)
        scheduler.start()
        scheduler.start()
        await scheduler.stop()
        await scheduler.stop()
