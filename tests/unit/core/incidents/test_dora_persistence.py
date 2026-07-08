"""Durability + round-trip tests for the DORA incident SQLite backend."""

from datetime import UTC, datetime, timedelta

import core.config.incidents as cfg
import core.incidents.dora_service as svc_mod
from core.incidents import (
    DoraClassification,
    DoraImpactAssessment,
    DoraIncident,
    DoraIncidentStatus,
    IncidentSeverity,
    InMemoryDoraIncidentStore,
)
from core.incidents.persistence import SQLiteDoraIncidentStore

T0 = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)


def _assessment() -> DoraImpactAssessment:
    return DoraImpactAssessment(
        critical_services_affected=True,
        clients_affected=True,
        data_losses=True,
    )


def _incident() -> DoraIncident:
    inc = DoraIncident(
        "outage",
        IncidentSeverity.CRITICAL,
        detected_at=T0,
        description="core banking outage",
        affected_systems=["ledger"],
        affected_clients=7,
        details={"region": "eu"},
    )
    inc.classification = DoraClassification(_assessment(), classified_at=T0)
    inc.status = DoraIncidentStatus.CLASSIFIED
    inc.initial_notification_at = T0 + timedelta(hours=3)
    return inc


class TestRoundTrip:
    def test_impact_assessment_round_trips(self):
        a = _assessment()
        assert DoraImpactAssessment.from_dict(a.to_dict()) == a

    def test_classification_round_trips(self):
        c = DoraClassification(_assessment(), classified_at=T0, major_override=True)
        assert DoraClassification.from_dict(c.to_dict()) == c

    def test_incident_round_trips(self):
        inc = _incident()
        assert DoraIncident.from_dict(inc.to_dict()) == inc

    def test_unclassified_incident_round_trips(self):
        inc = DoraIncident("blip", detected_at=T0)
        assert DoraIncident.from_dict(inc.to_dict()) == inc


class TestDurability:
    async def test_fresh_store_reads_back_everything(self, tmp_path):
        db = tmp_path / "dora.db"
        store = SQLiteDoraIncidentStore(str(db))
        inc = _incident()
        await store.save(inc)
        store.close()

        store2 = SQLiteDoraIncidentStore(str(db))
        got = await store2.get(inc.id)
        assert got == inc
        assert got.is_major is True
        assert await store2.list_all() == [inc]
        store2.close()

    async def test_missing_returns_none(self, tmp_path):
        store = SQLiteDoraIncidentStore(str(tmp_path / "dora.db"))
        assert await store.get("nope") is None
        store.close()


class TestWiring:
    def test_default_unset_builds_in_memory_store(self, monkeypatch):
        monkeypatch.delenv("DORA_DB_PATH", raising=False)
        monkeypatch.setattr(cfg, "_incident_config", None)
        monkeypatch.setattr(svc_mod, "_service", None)
        service = svc_mod.get_dora_incident_service()
        assert isinstance(service.store, InMemoryDoraIncidentStore)

    def test_env_path_selects_sqlite_store(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DORA_DB_PATH", str(tmp_path / "dora.db"))
        monkeypatch.setattr(cfg, "_incident_config", None)
        monkeypatch.setattr(svc_mod, "_service", None)
        service = svc_mod.get_dora_incident_service()
        assert isinstance(service.store, SQLiteDoraIncidentStore)
