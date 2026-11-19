"""SOC 2 readiness: scopes, controls, evidence, readiness, collection, package."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from baselith.core.compliance.soc2 import (
    CRITERIA,
    AttestationType,
    ControlFrequency,
    EvidenceDraft,
    EvidenceKind,
    EvidenceOutcome,
    InMemorySoc2Store,
    Soc2Control,
    Soc2Evidence,
    Soc2NotFoundError,
    Soc2Service,
    Soc2ValidationError,
    SQLiteSoc2Store,
    TrustCategory,
    assess_control,
    build_package,
    canonical_sha256,
    criteria_for,
    register_evidence_collector,
    render_markdown,
    starter_controls,
)
from baselith.core.compliance.soc2 import collectors as collectors_module
from baselith.core.compliance.soc2.types import Soc2Scope

NOW = datetime(2027, 6, 30, 12, 0, tzinfo=UTC)


def _ev(control: Soc2Control, days_ago: float, **kw: Any) -> Soc2Evidence:
    return Soc2Evidence(
        control_id=control.id,
        title="t",
        kind=EvidenceKind.MANUAL,
        source="manual",
        sha256="0" * 64,
        collected_at=NOW - timedelta(days=days_ago),
        **kw,
    )


def _control(frequency: ControlFrequency = ControlFrequency.WEEKLY) -> Soc2Control:
    return Soc2Control(title="c", criteria=["CC6.1"], frequency=frequency)


def _type_i() -> Soc2Scope:
    """A design examined as of the day before ``NOW``."""
    return Soc2Scope(
        name="s",
        attestation_type=AttestationType.TYPE_I,
        period_start=date(2027, 6, 30),
        period_end=date(2027, 6, 30),
    )


def _type_ii(start: date = date(2027, 1, 1)) -> Soc2Scope:
    return Soc2Scope(
        name="s", attestation_type=AttestationType.TYPE_II, period_start=start
    )


@pytest.fixture(autouse=True)
def _quiet_audit(monkeypatch) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    class _Logger:
        async def log(self, event_type, **kwargs):
            records.append({"event_type": event_type.value, **kwargs})

    for module in (
        "baselith.core.compliance.soc2.service",
        "baselith.core.compliance.soc2.package",
    ):
        monkeypatch.setattr(f"{module}.get_audit_logger", lambda: _Logger())
    return records


# -- catalogue -------------------------------------------------------------


def test_the_catalogue_holds_identifiers_and_short_titles_only():
    assert {c.category for c in CRITERIA.values()} == set(TrustCategory)
    assert all(len(c.title) < 80 for c in CRITERIA.values())
    assert "CC6.1" in CRITERIA and "P8.1" in CRITERIA and "A1.2" in CRITERIA


def test_security_is_always_in_scope():
    scope = Soc2Scope(name="s", categories=[TrustCategory.PRIVACY])

    assert scope.categories[0] is TrustCategory.SECURITY
    ids = {c.id for c in criteria_for(scope.categories)}
    assert "CC7.2" in ids and "P1.1" in ids and "A1.1" not in ids


# -- readiness: one control -------------------------------------------------


def test_a_control_without_evidence_is_untested():
    control = _control()

    result = assess_control(control, [], _type_ii(), NOW)

    assert result.status == "untested"
    assert result.evidence_count == 0


def test_recent_weekly_evidence_is_covered():
    control = _control()
    evidence = [_ev(control, d) for d in range(0, 180, 7)]

    result = assess_control(control, evidence, _type_ii(), NOW)

    assert result.status == "covered"
    assert result.coverage is not None and result.coverage >= 0.95


def test_evidence_older_than_the_frequency_allows_is_stale():
    control = _control(ControlFrequency.WEEKLY)

    assert assess_control(control, [_ev(control, 11)], _type_i(), NOW).status == "stale"
    assert (
        assess_control(control, [_ev(control, 10)], _type_i(), NOW).status == "covered"
    )


def test_a_latest_exception_outranks_everything_until_it_operates_again():
    control = _control()
    scope = _type_i()
    failing = [_ev(control, 3), _ev(control, 1, outcome=EvidenceOutcome.EXCEPTION)]
    fixed = [*failing, _ev(control, 0.1)]

    assert assess_control(control, failing, scope, NOW).status == "exception"
    recovered = assess_control(control, fixed, scope, NOW)
    assert recovered.status == "covered"
    assert recovered.exceptions_in_window == 1  # the deviation stays on the record


def test_type_ii_needs_consistent_evidence_not_one_recent_item():
    control = _control(ControlFrequency.WEEKLY)
    scope = _type_ii(date(2027, 1, 1))

    result = assess_control(control, [_ev(control, 0.5)], scope, NOW)

    assert result.status == "intermittent"
    assert result.coverage is not None and result.coverage < 0.1


def test_type_i_judges_the_design_at_a_date_and_ignores_coverage():
    control = _control(ControlFrequency.WEEKLY)
    scope = Soc2Scope(
        name="s",
        attestation_type=AttestationType.TYPE_I,
        period_start=date(2027, 6, 29),
        period_end=date(2027, 6, 29),
    )

    result = assess_control(control, [_ev(control, 2)], scope, NOW)

    assert result.status == "covered"
    assert result.coverage is None


def test_evidence_after_the_as_of_date_does_not_count():
    control = _control()
    scope = Soc2Scope(
        name="s",
        attestation_type=AttestationType.TYPE_I,
        period_start=date(2027, 6, 1),
        period_end=date(2027, 6, 1),
    )

    assert assess_control(control, [_ev(control, 2)], scope, NOW).status == "untested"


def test_event_driven_controls_have_no_staleness():
    control = _control(ControlFrequency.EVENT)

    result = assess_control(control, [_ev(control, 100)], _type_ii(), NOW)

    assert result.status == "covered"
    assert result.coverage is None


# -- service ---------------------------------------------------------------


async def test_a_criterion_takes_the_worst_status_of_its_controls():
    service = Soc2Service()
    scope = await service.create_scope(
        "FY27",
        attestation_type=AttestationType.TYPE_II,
        period_start=date(2027, 6, 1),
    )
    good = await service.add_control("Good", criteria=["CC6.1"])
    await service.add_control("Gap", criteria=["CC6.1"])
    await service.record_evidence(good.id, "e")

    _, criteria, _, summary = await service.readiness(scope.id, datetime.now(UTC))

    cc61 = next(c for c in criteria if c.criterion.id == "CC6.1")
    assert cc61.status == "untested"  # the second control has nothing
    cc72 = next(c for c in criteria if c.criterion.id == "CC7.2")
    assert cc72.status == "uncovered"
    assert summary["ready"] is False
    assert summary["counts"]["uncovered"] > 0


async def test_unknown_criteria_and_impossible_periods_are_refused():
    service = Soc2Service()

    with pytest.raises(Soc2ValidationError, match="CC99.9"):
        await service.add_control("x", criteria=["CC99.9"])
    with pytest.raises(Soc2ValidationError, match="ends before"):
        await service.create_scope(
            "x",
            attestation_type=AttestationType.TYPE_II,
            period_start=date(2027, 6, 1),
            period_end=date(2027, 5, 1),
        )
    with pytest.raises(Soc2NotFoundError):
        await service.record_evidence("nope", "e")


async def test_a_type_i_scope_defaults_its_as_of_date_to_the_start():
    scope = await Soc2Service().create_scope("s", period_start=date(2027, 3, 1))

    assert scope.period_end == date(2027, 3, 1)
    assert scope.as_of == date(2027, 3, 1)


async def test_manual_evidence_is_bound_to_what_it_points_at(_quiet_audit):
    service = Soc2Service()
    control = await service.add_control("c", criteria=["CC6.1"])

    from_text = await service.record_evidence(control.id, "e", content="the policy")
    given = await service.record_evidence(control.id, "e", content_sha256="AB" * 32)
    bare = await service.record_evidence(control.id, "e", reference="JIRA-1")

    import hashlib

    assert from_text.sha256 == hashlib.sha256(b"the policy").hexdigest()
    assert given.sha256 == "ab" * 32
    assert bare.sha256 == canonical_sha256(
        {"title": "e", "summary": "", "reference": "JIRA-1"}
    )
    with pytest.raises(Soc2ValidationError, match="64 hex"):
        await service.record_evidence(control.id, "e", content_sha256="nope")
    anchored = [r for r in _quiet_audit if r["action"] == "soc2_evidence"]
    assert [r["details"]["sha256"] for r in anchored] == [
        from_text.sha256,
        given.sha256,
        bare.sha256,
    ]


async def test_every_change_lands_on_the_audit_trail(_quiet_audit):
    service = Soc2Service()
    scope = await service.create_scope("s")
    control = await service.add_control("c", criteria=["CC6.1"])
    await service.update_control(control.id, owner="alice")
    await service.update_scope(scope.id, system_description="the platform")

    assert [r["action"] for r in _quiet_audit] == [
        "soc2_scope",
        "soc2_control",
        "soc2_control_update",
        "soc2_scope_update",
    ]
    assert all(r["details"]["regime"] == "soc2" for r in _quiet_audit)


async def test_starter_controls_are_added_once():
    service = Soc2Service()

    first = await service.seed_starter_controls("alice")
    again = await service.seed_starter_controls("alice")

    assert len(first) == len(starter_controls("x")) and again == []
    assert all(c.owner == "alice" and c.collectors for c in first)
    assert all(all(cr in CRITERIA for cr in c.criteria) for c in first)


# -- collection ------------------------------------------------------------


@pytest.fixture()
def fake_collectors(monkeypatch):
    monkeypatch.setattr(collectors_module, "_REGISTRY", {})

    async def fine() -> EvidenceDraft:
        return EvidenceDraft("fine", "all good", details={"n": 1})

    async def bad() -> EvidenceDraft:
        return EvidenceDraft("bad", "broken", EvidenceOutcome.EXCEPTION, {"n": 2})

    async def boom() -> EvidenceDraft:
        raise RuntimeError("source offline")

    register_evidence_collector("fine", fine, description="d")
    register_evidence_collector("bad", bad, description="d")
    register_evidence_collector("boom", boom, description="d")


async def test_collection_feeds_every_control_that_lists_the_collector(fake_collectors):
    service = Soc2Service()
    a = await service.add_control("a", criteria=["CC6.1"], collector_names=["fine"])
    b = await service.add_control(
        "b", criteria=["CC7.2"], collector_names=["fine", "bad"]
    )
    inactive = await service.add_control("c", collector_names=["fine"])
    await service.update_control(inactive.id, active=False)

    report = await service.collect("scheduler")

    assert report.collected == 3 and report.exceptions == 1
    assert report.collectors == {"bad": "exception", "fine": "ok"}
    assert len(await service.list_evidence(a.id)) == 1
    assert {e.source for e in await service.list_evidence(b.id)} == {"fine", "bad"}
    assert await service.list_evidence(inactive.id) == []
    stored = (await service.list_evidence(a.id))[0]
    assert stored.kind is EvidenceKind.AUTOMATIC
    assert stored.sha256 == canonical_sha256({"n": 1})
    assert stored.recorded_by == "scheduler"


async def test_a_collector_that_cannot_run_is_an_exception_not_a_silence(
    fake_collectors,
):
    service = Soc2Service()
    control = await service.add_control("c", collector_names=["boom", "ghost"])

    report = await service.collect()

    assert report.collectors == {"boom": "failed"}
    assert report.unknown == ["ghost"]
    (evidence,) = await service.list_evidence(control.id)
    assert evidence.outcome is EvidenceOutcome.EXCEPTION
    assert "source offline" in evidence.summary


async def test_the_builtin_collectors_report_what_they_find(monkeypatch):
    from baselith.core.compliance.soc2 import registered_collectors as registry

    monkeypatch.setattr(
        "baselith.core.observability.audit_setup.get_durable_audit_sink", lambda: None
    )
    draft = await registry()["audit_chain"].run()
    assert draft.outcome is EvidenceOutcome.EXCEPTION  # no durable trail: not a pass

    incidents = await registry()["incident_register"].run()
    assert incidents.outcome is EvidenceOutcome.OPERATED
    assert set(incidents.details["regimes"]) == {"nis2", "dora", "ai_act", "gdpr"}

    assert {
        "audit_chain",
        "incident_register",
        "declared_posture",
        "vendor_register",
    } <= set(registry())


# -- persistence -----------------------------------------------------------


async def test_the_sqlite_store_round_trips_everything(tmp_path):
    store = SQLiteSoc2Store(str(tmp_path / "soc2.db"))
    service = Soc2Service(store)
    scope = await service.create_scope(
        "FY27",
        categories=[TrustCategory.AVAILABILITY],
        attestation_type=AttestationType.TYPE_II,
        period_start=date(2027, 1, 1),
        period_end=date(2027, 12, 31),
        system_description="the platform",
    )
    control = await service.add_control("c", criteria=["CC6.1", "A1.2"], owner="a")
    evidence = await service.record_evidence(control.id, "e", content="x")

    reopened = SQLiteSoc2Store(str(tmp_path / "soc2.db"))
    assert (await reopened.get_scope(scope.id)).to_dict() == scope.to_dict()
    assert (await reopened.get_control(control.id)).to_dict() == control.to_dict()
    assert (await reopened.list_evidence(control.id))[0].to_dict() == evidence.to_dict()
    assert await reopened.list_evidence("other") == []
    store.close()
    reopened.close()


# -- the auditor's package -------------------------------------------------


async def test_the_package_maps_criteria_to_controls_to_evidence(_quiet_audit):
    service = Soc2Service(InMemorySoc2Store())
    scope = await service.create_scope(
        "FY27",
        attestation_type=AttestationType.TYPE_II,
        period_start=datetime.now(UTC).date(),
    )
    control = await service.add_control(
        "Access reviews", criteria=["CC6.1", "CC6.3"], owner="alice"
    )
    await service.record_evidence(control.id, "Q2 review", reference="JIRA-7")

    package = await build_package(service, scope.id)

    assert package["disclaimer"].startswith("This is a readiness package")
    assert package["summary"]["gaps"] > 0
    (row,) = package["controls"]
    assert row["evidence"][0]["reference"] == "JIRA-7"
    assert row["readiness"]["status"] in {"covered", "intermittent", "stale"}
    digest = package.pop("package_sha256")
    assert digest == canonical_sha256(package)
    exports = [r for r in _quiet_audit if r["action"] == "soc2_package"]
    assert exports[0]["details"]["package_sha256"] == digest


async def test_the_markdown_package_states_what_it_is_not(_quiet_audit):
    service = Soc2Service()
    scope = await service.create_scope("FY27")
    await service.add_control("c", criteria=["CC6.1"])

    text = render_markdown(await build_package(service, scope.id))

    assert text.startswith("# SOC 2 readiness package — FY27")
    assert "not a SOC 2 report" in text
    assert "| CC6.1 |" in text and "_No evidence recorded._" in text
