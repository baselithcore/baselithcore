"""The inference Qdrant settings bind the names the deploy files already use."""

from __future__ import annotations

import pytest

from core.config.inference import QdrantServerConfig


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "QDRANT_API_KEY",
        "BASELITH_QDRANT_API_KEY",
        "QDRANT_URL",
        "BASELITH_QDRANT_URL",
    ):
        monkeypatch.delenv(name, raising=False)


def test_plain_qdrant_names_bind(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QDRANT_API_KEY", "k1")
    monkeypatch.setenv("QDRANT_URL", "http://qdrant:6333")
    cfg = QdrantServerConfig()
    assert cfg.api_key is not None and cfg.api_key.get_secret_value() == "k1"
    assert cfg.url == "http://qdrant:6333"


def test_prefixed_names_still_bind_and_win(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QDRANT_API_KEY", "plain")
    monkeypatch.setenv("BASELITH_QDRANT_API_KEY", "specific")
    monkeypatch.setenv("BASELITH_QDRANT_URL", "http://inference-qdrant:6333")
    cfg = QdrantServerConfig()
    assert cfg.api_key is not None and cfg.api_key.get_secret_value() == "specific"
    assert cfg.url == "http://inference-qdrant:6333"
