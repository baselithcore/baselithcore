"""Durability + round-trip tests for the NIS2 incident SQLite backend."""

from datetime import UTC, datetime, timedelta

import core.config.incidents as cfg
import core.incidents.service as svc_mod
from core.incidents import (
    IncidentSeverity,
    IncidentStatus,
    InMemoryIncidentStore,
    SecurityIncident,
)
from core.incidents.persistence import SQLiteIncidentStore

T0 = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)


def _incident() -> SecurityIncident:
    inc = SecurityIncident(
        "breach",
        IncidentSeverity.CRITICAL,
        detected_at=T0,
        significant=True,
        description="sqli against api",
        affected_systems=["api", "db"],
        affected_subjects=42,
        details={"vector": "sqli", "nested": {"a": 1}},
    )
    inc.notification_at = T0 + timedelta(hours=50)
    inc.status = IncidentStatus.NOTIFICATION_SUBMITTED
    return inc


class TestRoundTrip:
    def test_security_incident_round_trips(self):
        inc = _incident()
        assert SecurityIncident.from_dict(inc.to_dict()) == inc

    def test_minimal_incident_round_trips(self):
        inc = SecurityIncident("x", IncidentSeverity.LOW, significant=False)
        assert SecurityIncident.from_dict(inc.to_dict()) == inc


class TestDurability:
    async def test_fresh_store_reads_back_everything(self, tmp_path):
        db = tmp_path / "incidents.db"
        store = SQLiteIncidentStore(str(db))
        inc = _incident()
        await store.save(inc)
        store.close()

        # A brand-new store over the same DB path must rehydrate the record.
        store2 = SQLiteIncidentStore(str(db))
        got = await store2.get(inc.id)
        assert got == inc
        assert await store2.list_all() == [inc]
        store2.close()

    async def test_upsert_overwrites(self, tmp_path):
        db = tmp_path / "incidents.db"
        store = SQLiteIncidentStore(str(db))
        inc = _incident()
        await store.save(inc)
        inc.status = IncidentStatus.CLOSED
        inc.closed_at = T0 + timedelta(days=40)
        await store.save(inc)
        store.close()

        store2 = SQLiteIncidentStore(str(db))
        got = await store2.get(inc.id)
        assert got is not None
        assert got.status is IncidentStatus.CLOSED
        assert len(await store2.list_all()) == 1
        store2.close()

    async def test_missing_returns_none(self, tmp_path):
        store = SQLiteIncidentStore(str(tmp_path / "incidents.db"))
        assert await store.get("nope") is None
        store.close()


class TestWiring:
    def test_default_unset_builds_in_memory_store(self, monkeypatch):
        monkeypatch.delenv("INCIDENT_DB_PATH", raising=False)
        monkeypatch.setattr(cfg, "_incident_config", None)
        monkeypatch.setattr(svc_mod, "_service", None)
        service = svc_mod.get_incident_service()
        assert isinstance(service.store, InMemoryIncidentStore)

    def test_env_path_selects_sqlite_store(self, monkeypatch, tmp_path):
        monkeypatch.setenv("INCIDENT_DB_PATH", str(tmp_path / "incidents.db"))
        monkeypatch.setattr(cfg, "_incident_config", None)
        monkeypatch.setattr(svc_mod, "_service", None)
        service = svc_mod.get_incident_service()
        assert isinstance(service.store, SQLiteIncidentStore)
