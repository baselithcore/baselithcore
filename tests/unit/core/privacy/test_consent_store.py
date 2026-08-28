"""Tests for the durable Art. 7 consent log."""

from __future__ import annotations

import threading

from core.privacy.consent import ConsentService, SQLiteConsentStore


class TestDurability:
    async def test_the_record_chain_survives_a_reopen(self, tmp_path):
        store = SQLiteConsentStore(tmp_path / "consent.db")
        service = ConsentService(store=store)
        await service.grant("s1", "marketing", notice_version="v3", evidence="form")
        await service.withdraw("s1", "marketing")
        store.close()

        reopened = SQLiteConsentStore(tmp_path / "consent.db")
        try:
            svc = ConsentService(store=reopened)
            # Art. 7(1): the proof must outlive the process.
            history = await svc.history("s1")
            assert len(history) == 2
            assert history[0].granted is True
            assert history[0].notice_version == "v3"
            assert await svc.has_consent("s1", "marketing") is False
        finally:
            reopened.close()

    async def test_order_is_preserved_within_the_same_clock_tick(self, tmp_path):
        store = SQLiteConsentStore(tmp_path / "consent.db")
        try:
            service = ConsentService(store=store)
            # Grant and withdrawal can land on the same float timestamp; the
            # sequence, not the clock, must decide the current state.
            await service.grant("s1", "p")
            await service.withdraw("s1", "p")
            await service.grant("s1", "p")
            history = await service.history("s1")
            assert [r.granted for r in history] == [True, False, True]
            assert await service.has_consent("s1", "p") is True
        finally:
            store.close()

    async def test_erasure_drops_the_subject(self, tmp_path):
        store = SQLiteConsentStore(tmp_path / "consent.db")
        try:
            service = ConsentService(store=store)
            await service.grant("s1", "a")
            await service.grant("s1", "b")
            await service.grant("s2", "a")
            assert await service.erase("s1") == 2
            assert await service.history("s1") == []
            # Another subject's proof is untouched.
            assert len(await service.history("s2")) == 1
        finally:
            store.close()

    async def test_subjects_are_isolated(self, tmp_path):
        store = SQLiteConsentStore(tmp_path / "consent.db")
        try:
            service = ConsentService(store=store)
            await service.grant("s1", "marketing")
            assert await service.has_consent("s2", "marketing") is False
            assert await service.active_purposes("s1") == ["marketing"]
        finally:
            store.close()


class TestConfiguration:
    def test_service_uses_the_durable_store_when_configured(
        self, tmp_path, monkeypatch
    ):
        from core.config import privacy as privacy_config
        from core.privacy.consent import get_consent_service, reset_consent_service

        monkeypatch.setenv("PRIVACY_CONSENT_DB_PATH", str(tmp_path / "c.db"))
        privacy_config._privacy_config = None
        reset_consent_service()
        try:
            service = get_consent_service()
            assert isinstance(service._store, SQLiteConsentStore)
            service._store.close()
        finally:
            reset_consent_service()
            privacy_config._privacy_config = None

    def test_service_defaults_to_in_memory(self, monkeypatch):
        from core.config import privacy as privacy_config
        from core.privacy.consent import (
            InMemoryConsentStore,
            get_consent_service,
            reset_consent_service,
        )

        monkeypatch.delenv("PRIVACY_CONSENT_DB_PATH", raising=False)
        privacy_config._privacy_config = None
        reset_consent_service()
        try:
            assert isinstance(
                get_consent_service()._store,
                InMemoryConsentStore,
            )
        finally:
            reset_consent_service()
            privacy_config._privacy_config = None


class _ThreadRecordingConnection:
    """Proxy that records which thread executes each statement."""

    def __init__(self, conn):
        self._conn = conn
        self.threads: list[int] = []

    def execute(self, *args, **kwargs):
        self.threads.append(threading.get_ident())
        return self._conn.execute(*args, **kwargs)

    def close(self) -> None:
        self._conn.close()


class TestEventLoopIsNotBlocked:
    """SQLite is blocking disk I/O — it must never run on the event loop thread."""

    async def test_statements_run_on_a_worker_thread(self, tmp_path):
        store = SQLiteConsentStore(tmp_path / "consent.db")
        probe = _ThreadRecordingConnection(store._conn)
        store._conn = probe  # type: ignore[assignment]
        loop_thread = threading.get_ident()
        try:
            service = ConsentService(store=store)
            await service.grant("s1", "marketing")
            assert len(await service.history("s1")) == 1
            assert await store.drop_subject("s1") == 1
        finally:
            store.close()

        assert len(probe.threads) == 3
        assert loop_thread not in probe.threads
