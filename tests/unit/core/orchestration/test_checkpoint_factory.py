"""Tests for the default checkpoint-store factory."""

from __future__ import annotations

import pytest

import baselith.core.orchestration.checkpoint_factory as factory
from baselith.core.orchestration.checkpoint import InMemoryCheckpointStore


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    factory.reset_default_checkpoint_store()
    # Isolate from process env / cached config.
    import baselith.core.config.orchestration as orch_config

    monkeypatch.setattr(orch_config, "_orchestration_config", None)
    yield
    factory.reset_default_checkpoint_store()
    monkeypatch.setattr(orch_config, "_orchestration_config", None)


def test_enabled_by_default_resolves_a_store(monkeypatch):
    monkeypatch.delenv("ORCHESTRATOR_CHECKPOINT_ENABLED", raising=False)
    monkeypatch.setenv("ORCHESTRATOR_CHECKPOINT_BACKEND", "memory")
    assert isinstance(factory.get_default_checkpoint_store(), InMemoryCheckpointStore)


def test_explicitly_disabled_returns_none(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_CHECKPOINT_ENABLED", "false")
    assert factory.get_default_checkpoint_store() is None


def test_memory_backend(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_CHECKPOINT_ENABLED", "true")
    monkeypatch.setenv("ORCHESTRATOR_CHECKPOINT_BACKEND", "memory")
    store = factory.get_default_checkpoint_store()
    assert isinstance(store, InMemoryCheckpointStore)
    # Singleton: repeated calls return the same store.
    assert factory.get_default_checkpoint_store() is store


def test_unknown_backend_falls_back_to_memory(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_CHECKPOINT_ENABLED", "true")
    monkeypatch.setenv("ORCHESTRATOR_CHECKPOINT_BACKEND", "cassandra")
    assert isinstance(factory.get_default_checkpoint_store(), InMemoryCheckpointStore)


async def test_initialize_is_idempotent(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_CHECKPOINT_ENABLED", "true")
    monkeypatch.setenv("ORCHESTRATOR_CHECKPOINT_BACKEND", "memory")
    store1 = await factory.initialize_default_checkpoint_store()
    store2 = await factory.initialize_default_checkpoint_store()
    assert store1 is store2 is factory.get_default_checkpoint_store()


async def test_initialize_disabled_returns_none(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_CHECKPOINT_ENABLED", "false")
    assert await factory.initialize_default_checkpoint_store() is None


class _FakePostgresStore:
    """Stands in for PostgresCheckpointStore without a database."""

    def __init__(self, **_kwargs: object) -> None:
        self.initialize_calls = 0

    async def initialize(self) -> None:
        self.initialize_calls += 1


@pytest.fixture
def _postgres_store(monkeypatch):
    from baselith.core.db import reachability
    from baselith.core.orchestration import checkpoint_postgres

    monkeypatch.setattr(
        checkpoint_postgres, "PostgresCheckpointStore", _FakePostgresStore
    )
    monkeypatch.setenv("ORCHESTRATOR_CHECKPOINT_ENABLED", "true")
    monkeypatch.setenv("ORCHESTRATOR_CHECKPOINT_BACKEND", "postgres")
    reachability.reset_postgres_probe()
    yield
    reachability.reset_postgres_probe()


async def test_postgres_store_init_fails_fast_when_the_probe_saw_the_db_down(
    _postgres_store,
):
    from baselith.core.db import reachability

    reachability._last_outcome = False
    with pytest.raises(factory.CheckpointStoreUnavailableError):
        await factory.initialize_default_checkpoint_store()
    store = factory.get_default_checkpoint_store()
    assert store.initialize_calls == 0
    assert factory.is_default_checkpoint_store_initialized() is False

    # The database came back: the next attempt initializes the same store.
    reachability._last_outcome = True
    assert await factory.initialize_default_checkpoint_store() is store
    assert store.initialize_calls == 1
    assert factory.is_default_checkpoint_store_initialized() is True


async def test_postgres_store_init_unchanged_when_never_probed(_postgres_store):
    store = await factory.initialize_default_checkpoint_store()
    assert store.initialize_calls == 1


async def test_memory_store_ignores_the_probe(monkeypatch):
    from baselith.core.db import reachability

    monkeypatch.setenv("ORCHESTRATOR_CHECKPOINT_ENABLED", "true")
    monkeypatch.setenv("ORCHESTRATOR_CHECKPOINT_BACKEND", "memory")
    reachability._last_outcome = False
    try:
        assert isinstance(
            await factory.initialize_default_checkpoint_store(),
            InMemoryCheckpointStore,
        )
    finally:
        reachability.reset_postgres_probe()


class _Logger:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def warning(self, msg: str, *args, **kwargs) -> None:
        self.warnings.append(msg % args if args else msg)

    def info(self, *args, **kwargs) -> None:
        pass


def _postgres_storage(enabled: bool):
    from types import SimpleNamespace

    return lambda: SimpleNamespace(postgres_enabled=enabled)


def test_auto_resolving_to_memory_warns_that_runs_will_not_survive(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_CHECKPOINT_ENABLED", "true")
    monkeypatch.setenv("ORCHESTRATOR_CHECKPOINT_BACKEND", "auto")
    monkeypatch.setattr(
        "baselith.core.config.storage.get_storage_config", _postgres_storage(False)
    )
    log = _Logger()
    monkeypatch.setattr(factory, "logger", log)

    assert isinstance(factory.get_default_checkpoint_store(), InMemoryCheckpointStore)
    assert any("checkpoint_backend_not_durable" in w for w in log.warnings)
    assert any("will NOT survive a restart" in w for w in log.warnings)


def test_an_explicit_memory_backend_is_not_warned_about(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_CHECKPOINT_ENABLED", "true")
    monkeypatch.setenv("ORCHESTRATOR_CHECKPOINT_BACKEND", "memory")
    log = _Logger()
    monkeypatch.setattr(factory, "logger", log)

    factory.get_default_checkpoint_store()
    assert log.warnings == []


@pytest.mark.parametrize(
    ("configured", "durable", "expected"),
    [
        (None, True, True),
        (None, False, False),
        ("false", True, False),
        ("true", False, True),
    ],
)
def test_auto_resume_defaults_on_for_a_durable_store(
    monkeypatch, tmp_path, configured, durable, expected
):
    from baselith.core.orchestration.checkpoint_sqlite import SQLiteCheckpointStore

    if configured is None:
        monkeypatch.delenv("ORCHESTRATOR_CHECKPOINT_RESUME_ON_STARTUP", raising=False)
    else:
        monkeypatch.setenv("ORCHESTRATOR_CHECKPOINT_RESUME_ON_STARTUP", configured)
    store = (
        SQLiteCheckpointStore(tmp_path / "c.db")
        if durable
        else InMemoryCheckpointStore()
    )
    try:
        assert factory.is_durable_store(store) is durable
        assert factory.auto_resume_enabled(store) is expected
    finally:
        if durable:
            store.close()


def test_resume_on_startup_is_unset_by_default(monkeypatch):
    from baselith.core.config.orchestration import OrchestrationConfig

    monkeypatch.delenv("ORCHESTRATOR_CHECKPOINT_RESUME_ON_STARTUP", raising=False)
    assert OrchestrationConfig().checkpoint_resume_on_startup is None
