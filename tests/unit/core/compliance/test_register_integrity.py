"""Records that must not be lost, replaced or quietly downgraded."""

from __future__ import annotations

import asyncio
import stat
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from baselith.core.compliance.persistence import (
    RecordExistsError,
    SQLiteAiSystemStore,
    SQLitePostMarketStore,
)
from baselith.core.compliance.post_market import (
    MonitoringMetric,
    PostMarketMonitoringPlan,
)
from baselith.core.compliance.post_market_service import PostMarketService
from baselith.core.compliance.prohibited import ProhibitedPractice
from baselith.core.compliance.registry import AiSystemRegistry
from baselith.core.compliance.soc2 import (
    AttestationType,
    EvidenceDraft,
    EvidenceKind,
    EvidenceOutcome,
    InMemorySoc2Store,
    Soc2Control,
    Soc2Evidence,
    Soc2Service,
    Soc2ValidationError,
    SQLiteSoc2Store,
    assess_control,
    build_package,
    register_evidence_collector,
    render_markdown,
)
from baselith.core.compliance.soc2 import collectors as collectors_module
from baselith.core.compliance.soc2 import package as package_module
from baselith.core.compliance.soc2.types import Soc2Scope
from baselith.core.compliance.types import AiSystem, LifecycleStage, RiskCategory

NOW = datetime(2027, 6, 30, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _quiet_audit(monkeypatch) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    class _Logger:
        enabled = True

        async def log(self, event_type, **kwargs):
            records.append({"event_type": event_type.value, **kwargs})

        async def log_event(self, event):
            records.append(event.to_dict())

    for module in (
        "baselith.core.compliance.soc2.service",
        "baselith.core.compliance.soc2.package",
        "baselith.core.compliance.post_market_service",
        "baselith.core.compliance.registry",
        "baselith.core.observability.audit",
    ):
        monkeypatch.setattr(f"{module}.get_audit_logger", lambda: _Logger())
    return records


def _plan() -> PostMarketMonitoringPlan:
    return PostMarketMonitoringPlan(
        system_id="sys-1",
        metrics=[MonitoringMetric(name="error_rate", threshold=0.05)],
    )


# -- post-market observations ----------------------------------------------


async def test_overlapping_observations_are_all_kept(tmp_path):
    store = SQLitePostMarketStore(tmp_path / "pm.db")
    service = PostMarketService(store)
    plan = await service.save(_plan())

    await asyncio.gather(
        *(service.observe(plan.id, "error_rate", 0.9) for _ in range(40))
    )

    stored = await service.require(plan.id)
    assert len(stored.observations) == 40
    store.close()


async def test_two_services_on_one_file_do_not_overwrite_each_other(tmp_path):
    # Two API workers: each holds its own connection to the same database.
    first = PostMarketService(SQLitePostMarketStore(tmp_path / "pm.db"))
    second = PostMarketService(SQLitePostMarketStore(tmp_path / "pm.db"))
    plan = await first.save(_plan())

    await asyncio.gather(
        *(
            (first if i % 2 else second).observe(plan.id, "error_rate", 0.01)
            for i in range(30)
        )
    )

    assert len((await first.require(plan.id)).observations) == 30


async def test_a_review_does_not_drop_a_concurrent_observation(tmp_path):
    service = PostMarketService(SQLitePostMarketStore(tmp_path / "pm.db"))
    plan = await service.save(_plan())

    await asyncio.gather(
        service.review(plan.id),
        *(service.observe(plan.id, "error_rate", 0.01) for _ in range(10)),
    )

    stored = await service.require(plan.id)
    assert len(stored.observations) == 10 and stored.last_reviewed_at is not None


async def test_a_store_without_update_is_serialised_in_process():
    class _Legacy:
        def __init__(self) -> None:
            self.plans: dict[str, dict[str, Any]] = {}

        async def save(self, plan):
            await asyncio.sleep(0)
            self.plans[plan.id] = plan.to_dict()

        async def get(self, plan_id):
            await asyncio.sleep(0)
            raw = self.plans.get(plan_id)
            return PostMarketMonitoringPlan.from_dict(raw) if raw else None

        async def list_all(self):
            return [PostMarketMonitoringPlan.from_dict(p) for p in self.plans.values()]

        async def delete(self, plan_id):
            return self.plans.pop(plan_id, None) is not None

    service = PostMarketService(_Legacy())
    plan = await service.save(_plan())

    await asyncio.gather(
        *(service.observe(plan.id, "error_rate", 0.01) for _ in range(20))
    )

    assert len((await service.require(plan.id)).observations) == 20


async def test_an_unknown_plan_and_an_undeclared_metric_keep_their_errors():
    service = PostMarketService()
    plan = await service.save(_plan())

    with pytest.raises(KeyError):
        await service.observe(plan.id, "not_declared", 1.0)
    with pytest.raises(LookupError):
        await service.observe("missing", "error_rate", 1.0)
    assert (await service.require(plan.id)).observations == []


# -- Art. 5 declaration ----------------------------------------------------


async def test_reclassifying_does_not_take_a_system_out_of_prohibited(tmp_path):
    registry = AiSystemRegistry(SQLiteAiSystemStore(tmp_path / "systems.db"))
    system, _ = await registry.register(
        AiSystem(name="scorer"),
        prohibited_practices=[ProhibitedPractice.SOCIAL_SCORING],
    )
    assert system.risk_category is RiskCategory.PROHIBITED

    again, result = await registry.reclassify(system.id)

    assert result.category is RiskCategory.PROHIBITED
    assert again.prohibited_practices == ["social_scoring"]
    # ...and across a restart, from the stored record.
    reloaded = await registry.require(system.id)
    assert reloaded.prohibited_practices == ["social_scoring"]


async def test_withdrawing_the_declaration_is_explicit_and_audited(_quiet_audit):
    registry = AiSystemRegistry()
    system, _ = await registry.register(
        AiSystem(name="scorer"),
        prohibited_practices=[ProhibitedPractice.SOCIAL_SCORING],
    )

    _, result = await registry.reclassify(system.id, prohibited_practices=[])

    assert result.category is not RiskCategory.PROHIBITED
    record = next(r for r in _quiet_audit if r.get("action") == "reclassify")
    assert record["details"]["prohibited_practices_before"] == ["social_scoring"]
    assert record["details"]["prohibited_practices"] == []


async def test_a_record_written_before_the_field_existed_still_loads():
    payload = AiSystem(name="old").to_dict()
    payload.pop("prohibited_practices")

    assert AiSystem.from_dict(payload).prohibited_practices == []
    assert LifecycleStage(payload["lifecycle_stage"])  # the rest is untouched


# -- SOC 2 readiness and evidence ------------------------------------------


def _scope() -> Soc2Scope:
    return Soc2Scope(
        name="s",
        attestation_type=AttestationType.TYPE_I,
        period_start=NOW.date(),
        period_end=NOW.date(),
    )


def _evidence(control: Soc2Control, minutes_ago: float, **kw: Any) -> Soc2Evidence:
    fields: dict[str, Any] = {
        "control_id": control.id,
        "title": "t",
        "kind": EvidenceKind.AUTOMATIC,
        "source": "audit_chain",
        "sha256": "0" * 64,
        "collected_at": NOW - timedelta(minutes=minutes_ago),
    }
    return Soc2Evidence(**{**fields, **kw})


def test_another_collector_running_later_does_not_hide_a_deviation():
    control = Soc2Control(title="c", criteria=["CC7.2"])
    evidence = [
        _evidence(control, 2, outcome=EvidenceOutcome.EXCEPTION),
        _evidence(control, 1, source="vendor_register"),
    ]

    assert assess_control(control, evidence, _scope(), NOW).status == "exception"


def test_a_note_does_not_close_an_exception_but_a_rerun_does():
    control = Soc2Control(title="c", criteria=["CC7.2"])
    broken = _evidence(control, 3, outcome=EvidenceOutcome.EXCEPTION)
    note = _evidence(control, 2, source="manual", outcome=EvidenceOutcome.INFORMATIONAL)
    same_source_note = _evidence(control, 2, outcome=EvidenceOutcome.INFORMATIONAL)
    fixed = _evidence(control, 1)

    for later in (note, same_source_note):
        status = assess_control(control, [broken, later], _scope(), NOW).status
        assert status == "exception"
    assert assess_control(control, [broken, note, fixed], _scope(), NOW).status == (
        "covered"
    )


async def test_the_assessment_runs_off_the_event_loop_and_reads_each_table_once(
    monkeypatch,
):
    import threading

    from baselith.core.compliance.soc2 import service as service_module

    service = Soc2Service()
    scope = await service.create_scope(
        "FY27",
        attestation_type=AttestationType.TYPE_II,
        period_start=datetime.now(UTC).date(),
    )
    for i in range(3):
        control = await service.add_control(f"c{i}", criteria=["CC6.1"])
        await service.record_evidence(control.id, "review")

    threads: list[str] = []
    real = service_module.assess_scope

    def _recording(*args, **kwargs):
        threads.append(threading.current_thread().name)
        return real(*args, **kwargs)

    monkeypatch.setattr(service_module, "assess_scope", _recording)
    reads = {"controls": 0, "evidence": 0}
    list_controls, list_evidence = service.list_controls, service.list_evidence

    async def _controls():
        reads["controls"] += 1
        return await list_controls()

    async def _evidence(control_id=None):
        reads["evidence"] += 1
        return await list_evidence(control_id)

    monkeypatch.setattr(service, "list_controls", _controls)
    monkeypatch.setattr(service, "list_evidence", _evidence)

    package = await build_package(service, scope.id)

    assert threads and threads[0] != threading.current_thread().name
    assert reads == {"controls": 1, "evidence": 1}
    assert [c["readiness"]["evidence_count"] for c in package["controls"]] == [1, 1, 1]


def test_each_control_is_judged_on_its_own_evidence_only():
    from baselith.core.compliance.soc2 import assess_scope

    quiet = Soc2Control(title="quiet", criteria=["CC6.1"])
    broken = Soc2Control(title="broken", criteria=["CC7.2"])
    evidence = [
        _evidence(quiet, 5),
        _evidence(broken, 4, outcome=EvidenceOutcome.EXCEPTION),
    ]

    _, assessed = assess_scope(_scope(), [quiet, broken], evidence, NOW)

    assert {c.title: (c.status, c.evidence_count) for c in assessed} == {
        "quiet": ("covered", 1),
        "broken": ("exception", 1),
    }


@pytest.mark.parametrize("durable", [False, True])
async def test_evidence_is_written_once(tmp_path, durable):
    store = (
        SQLiteSoc2Store(str(tmp_path / "soc2.db")) if durable else InMemorySoc2Store()
    )
    control = Soc2Control(title="c", criteria=["CC7.2"])
    original = _evidence(control, 5, outcome=EvidenceOutcome.EXCEPTION)
    await store.save_evidence(original)

    replacement = _evidence(control, 5, id=original.id)
    with pytest.raises(RecordExistsError):
        await store.save_evidence(replacement)

    kept = await store.get_evidence(original.id)
    assert kept is not None and kept.outcome is EvidenceOutcome.EXCEPTION


async def test_a_naive_collection_time_is_refused():
    service = Soc2Service()
    control = await service.add_control("c", criteria=["CC6.1"])

    with pytest.raises(Soc2ValidationError, match="timezone"):
        await service.record_evidence(
            control.id,
            "t",
            collected_at=datetime(2027, 6, 1, 9, 0),  # noqa: DTZ001 - the case
        )
    assert await service.list_evidence() == []


async def _collected(monkeypatch) -> tuple[Soc2Service, str, Soc2Evidence]:
    monkeypatch.setattr(collectors_module, "_REGISTRY", {})

    async def fine() -> EvidenceDraft:
        return EvidenceDraft("fine", "ok", details={2: "b", 10: "a", "at": NOW})

    register_evidence_collector("fine", fine, description="d")
    service = Soc2Service()
    scope = await service.create_scope(
        "FY27",
        attestation_type=AttestationType.TYPE_II,
        period_start=datetime.now(UTC).date(),
    )
    await service.add_control("c", criteria=["CC6.1"], collector_names=["fine"])
    await service.collect("scheduler")
    (evidence,) = await service.list_evidence()
    return service, scope.id, evidence


async def test_the_package_recomputes_each_digest(monkeypatch):
    service, scope_id, evidence = await _collected(monkeypatch)

    clean = await build_package(service, scope_id)

    (row,) = clean["controls"][0]["evidence"]
    assert row["integrity"] == {"digest": "ok", "anchor": "no_trail"}
    assert clean["evidence_integrity"]["ok"] is True

    evidence.details["2"] = "edited in the store"
    tampered = await build_package(service, scope_id)

    assert tampered["evidence_integrity"]["digest_mismatch"] == [evidence.id]
    assert tampered["evidence_integrity"]["ok"] is False
    assert "**1 failed**" in render_markdown(tampered)


async def test_the_package_checks_each_digest_against_the_trail(
    monkeypatch, _quiet_audit
):
    service, scope_id, evidence = await _collected(monkeypatch)

    async def trail(digest: str | None):
        async def _anchored() -> dict[str, str]:
            return {} if digest is None else {evidence.id: digest}

        monkeypatch.setattr(package_module, "_anchored_digests", _anchored)
        package = await build_package(service, scope_id)
        return package["controls"][0]["evidence"][0]["integrity"]["anchor"], package

    assert (await trail(evidence.sha256))[0] == "matched"
    assert (await trail(None))[0] == "missing"
    state, swapped = await trail("f" * 64)

    assert state == "mismatch"
    assert swapped["evidence_integrity"]["anchor_mismatch"] == [evidence.id]
    export = [r for r in _quiet_audit if r.get("action") == "soc2_package"][-1]
    assert export["success"] is False


async def test_the_trail_lookup_reads_what_recording_wrote(tmp_path, monkeypatch):
    from baselith.core.observability import audit_setup
    from baselith.core.observability.audit import AuditEvent, AuditEventType
    from baselith.core.observability.audit_chain import SQLiteAuditSink

    sink = SQLiteAuditSink(tmp_path / "audit.db")
    monkeypatch.setattr(audit_setup, "get_durable_audit_sink", lambda: sink)
    for evidence_id, digest in (("e1", "a" * 64), ("e1", "b" * 64), ("e2", "c" * 64)):
        await sink.write(
            AuditEvent(
                AuditEventType.COMPLIANCE_ASSESSMENT,
                resource=evidence_id,
                action="soc2_evidence",
                details={"regime": "soc2", "sha256": digest},
            )
        )
    await sink.write(
        AuditEvent(AuditEventType.COMPLIANCE_ASSESSMENT, action="soc2_package")
    )

    # The first record under an id is the original; a later one is not.
    assert await package_module._anchored_digests() == {
        "e1": "a" * 64,
        "e2": "c" * 64,
    }
    sink.close()


# -- files -----------------------------------------------------------------


def test_the_stores_are_readable_by_their_owner_only(tmp_path):
    from baselith.core.incidents.persistence import SQLiteIncidentStore
    from baselith.core.privacy.automated_decisions import (
        SQLiteAutomatedDecisionStore,
    )
    from baselith.core.privacy.consent import SQLiteConsentStore
    from baselith.core.privacy.requests import SQLiteDsrRequestStore
    from baselith.core.thirdparty.persistence import SQLiteRegisterStore

    stores = {
        "systems.db": SQLiteAiSystemStore,
        "incidents.db": SQLiteIncidentStore,
        "dsr.db": SQLiteDsrRequestStore,
        "register.db": SQLiteRegisterStore,
        "consent.db": SQLiteConsentStore,
        "decisions.db": SQLiteAutomatedDecisionStore,
    }
    for name, factory in stores.items():
        path = tmp_path / name
        factory(path)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600, name
