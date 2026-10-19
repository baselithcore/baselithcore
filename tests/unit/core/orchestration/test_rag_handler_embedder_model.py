"""The RAG handler embeds queries with the model that built the index.

``ChatConfig`` has no ``embedder_model``; the handler used to fall back to the
literal ``all-MiniLM-L6-v2``, which bypassed the remote embedding server,
reached for the Hugging Face Hub (an error inside an egress-locked customer
cell) and produced 384-dim vectors for a 1024-dim bge-m3 index.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import baselith.core.nlp as nlp
from baselith.core.orchestration.handlers.rag import StandardRagHandler


def _model_name_for(monkeypatch: pytest.MonkeyPatch, config: Any) -> str:
    seen: dict[str, Any] = {}

    class _Lazy:
        def __init__(self, factory: Any, model_name: str) -> None:
            seen["model"] = model_name

    monkeypatch.setattr(nlp, "LazyEmbedder", _Lazy)
    handler = StandardRagHandler.__new__(StandardRagHandler)
    handler._config = config
    handler._embedder = None
    assert handler.embedder is not None
    return seen["model"]


def test_defaults_to_the_vector_store_model(monkeypatch: pytest.MonkeyPatch) -> None:
    import baselith.core.config as config

    monkeypatch.setattr(
        config,
        "get_vectorstore_config",
        lambda: SimpleNamespace(embedding_model="BAAI/bge-m3"),
    )
    assert _model_name_for(monkeypatch, SimpleNamespace()) == "BAAI/bge-m3"


def test_an_explicit_embedder_model_still_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    assert (
        _model_name_for(monkeypatch, SimpleNamespace(embedder_model="custom/model"))
        == "custom/model"
    )
