"""Tests for the default checkpoint-store factory."""

from __future__ import annotations

import pytest

import core.orchestration.checkpoint_factory as factory
from core.orchestration.checkpoint import InMemoryCheckpointStore


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    factory.reset_default_checkpoint_store()
    # Isolate from process env / cached config.
    import core.config.orchestration as orch_config

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
