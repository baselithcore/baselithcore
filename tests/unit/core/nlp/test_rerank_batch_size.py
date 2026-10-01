"""``score_pairs`` must not leave a CrossEncoder on the default batch of 32.

The default is a padding trap off CUDA (same scores, ~40% slower on CPU), so
a real cross-encoder gets a device-appropriate ``batch_size``; any other
reranker keeps the bare ``predict(pairs)`` call its protocol promises.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from core.nlp.lazy import LazyReranker
from core.nlp.rerank import (
    CPU_BATCH_SIZE,
    CUDA_BATCH_SIZE,
    batch_size_for,
    score_pairs,
)

PAIRS = [("q", "a"), ("q", "b")]


class _Device:
    def __init__(self, kind: str) -> None:
        self.type = kind


class _FakeCrossEncoder:
    """Stands in for ``sentence_transformers.CrossEncoder``."""

    def __init__(self, device: str = "cpu") -> None:
        self.device = _Device(device)
        self.calls: list[tuple[Any, dict[str, Any]]] = []

    def predict(self, pairs: Any, **kwargs: Any) -> list[float]:
        self.calls.append((pairs, kwargs))
        return [0.1] * len(pairs)


class _PlainReranker:
    """A protocol-only reranker: ``predict`` takes no keywords at all."""

    def __init__(self) -> None:
        self.seen: Any = None

    def predict(self, pairs: Any) -> list[float]:
        self.seen = pairs
        return [0.2] * len(pairs)


@pytest.fixture
def fake_st(monkeypatch: pytest.MonkeyPatch) -> None:
    module = types.ModuleType("sentence_transformers")
    module.CrossEncoder = _FakeCrossEncoder  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)


@pytest.mark.parametrize(
    ("device", "expected"),
    [("cpu", CPU_BATCH_SIZE), ("mps", CPU_BATCH_SIZE), ("cuda", CUDA_BATCH_SIZE)],
)
def test_cross_encoder_gets_the_device_batch_size(
    fake_st: None, device: str, expected: int
) -> None:
    model = _FakeCrossEncoder(device)

    assert score_pairs(model, PAIRS) == [0.1, 0.1]

    ((pairs, kwargs),) = model.calls
    assert pairs == PAIRS
    assert kwargs == {"batch_size": expected, "show_progress_bar": False}


def test_lazy_reranker_is_resolved_before_choosing(fake_st: None) -> None:
    model = _FakeCrossEncoder("cpu")
    lazy = LazyReranker(lambda _name: model, "any-model")

    score_pairs(lazy, PAIRS)

    assert model.calls[0][1]["batch_size"] == CPU_BATCH_SIZE


def test_other_rerankers_keep_the_plain_protocol_call(fake_st: None) -> None:
    reranker = _PlainReranker()

    assert score_pairs(reranker, PAIRS) == [0.2, 0.2]
    assert reranker.seen == PAIRS


def test_without_sentence_transformers_nothing_is_a_cross_encoder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delitem(sys.modules, "sentence_transformers", raising=False)
    reranker = _PlainReranker()

    score_pairs(reranker, PAIRS)

    assert reranker.seen == PAIRS


def test_a_model_without_a_device_counts_as_cpu() -> None:
    assert batch_size_for(object()) == CPU_BATCH_SIZE
    assert batch_size_for(types.SimpleNamespace(device="cuda:0")) == CUDA_BATCH_SIZE
