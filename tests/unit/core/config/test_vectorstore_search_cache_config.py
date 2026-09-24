"""The search result cache must be configurable from the environment.

``SearchOrchestrator`` and ``VectorStoreService`` read ``search_cache_enabled``
and ``search_cache_ttl`` off the config with ``getattr`` — but neither was a
declared ``VectorStoreConfig`` field, and the model ignores unknown keys. An
env-driven deployment therefore could neither turn the cache off nor change its
300-second TTL: setting either variable did nothing, silently.
"""

import pytest
from pydantic import ValidationError

from core.config.vectorstore import VectorStoreConfig
from core.services.vectorstore.orchestrator import SearchOrchestrator

pytestmark = pytest.mark.unit


def test_defaults_keep_the_previous_behaviour() -> None:
    config = VectorStoreConfig()

    assert config.search_cache_enabled is True
    assert config.search_cache_ttl == 300


def test_environment_binds_both_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VECTORSTORE_SEARCH_CACHE_ENABLED", "false")
    monkeypatch.setenv("VECTORSTORE_SEARCH_CACHE_TTL", "45")

    config = VectorStoreConfig()

    assert config.search_cache_enabled is False
    assert config.search_cache_ttl == 45


def test_the_orchestrator_honours_the_bound_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VECTORSTORE_SEARCH_CACHE_ENABLED", "false")
    monkeypatch.setenv("VECTORSTORE_SEARCH_CACHE_TTL", "45")

    orchestrator = SearchOrchestrator(VectorStoreConfig(), provider=None)

    assert orchestrator._search_cache_enabled is False
    assert orchestrator._search_cache_ttl == 45


def test_a_non_positive_ttl_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zero reads as "never expire" to some cache backends — refuse it outright."""
    monkeypatch.setenv("VECTORSTORE_SEARCH_CACHE_TTL", "0")

    with pytest.raises(ValidationError):
        VectorStoreConfig()
