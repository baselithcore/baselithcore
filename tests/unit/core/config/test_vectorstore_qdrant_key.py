"""The vector store reads the Qdrant key under the name the Helm chart ships it.

The chart and the customer provisioner put the cell's Qdrant key in the Secret
as ``BASELITH_QDRANT_API_KEY`` (the inference settings' name). The vector store
used to read only ``QDRANT_API_KEY``, so every memory search against a cell's
own Qdrant went out unauthenticated and came back 401.
"""

from __future__ import annotations

import pytest

from baselith.core.config.vectorstore import VectorStoreConfig


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("QDRANT_API_KEY", "BASELITH_QDRANT_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def _key(cfg: VectorStoreConfig) -> str | None:
    return cfg.qdrant_api_key.get_secret_value() if cfg.qdrant_api_key else None


def test_chart_name_binds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BASELITH_QDRANT_API_KEY", "from-chart")
    assert _key(VectorStoreConfig()) == "from-chart"


def test_plain_name_still_binds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QDRANT_API_KEY", "plain")
    assert _key(VectorStoreConfig()) == "plain"


def test_specific_name_wins_like_the_inference_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QDRANT_API_KEY", "plain")
    monkeypatch.setenv("BASELITH_QDRANT_API_KEY", "specific")
    assert _key(VectorStoreConfig()) == "specific"


def test_field_name_still_works_in_code() -> None:
    assert _key(VectorStoreConfig(qdrant_api_key="direct")) == "direct"
