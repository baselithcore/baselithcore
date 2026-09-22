"""Enabling evaluation after import must actually enable the DeepEval metrics.

``core.evaluation.metrics`` decided whether DeepEval was usable **at import
time**, from ``evaluation_config.is_enabled`` — which defaults to ``False``.
``scripts/run_eval.py`` imports the module first and flips
``evaluation_config.enabled = True`` afterwards, so by then the decision was
already made: every evaluator was built without a metric, every case logged
"Skipping" and scored ``0.0``, and a run that measured nothing looked like a
run in which every answer was unfaithful.

The decision is now taken when an evaluator is constructed. These tests stand
DeepEval in with fake modules, so they run whether or not it is installed.
"""

import sys
import types
from typing import Any

import pytest

from core.config.evaluation import evaluation_config
from core.evaluation import metrics

pytestmark = pytest.mark.unit


class _FakeMetric:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.score = 0.9
        self.reason = "fake"

    def measure(self, test_case: Any) -> None:
        self.test_case = test_case


class _FakeTestCase:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


@pytest.fixture
def fake_deepeval(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install stand-in ``deepeval`` modules and reset the module's cached state."""
    root = types.ModuleType("deepeval")
    metrics_mod = types.ModuleType("deepeval.metrics")
    metrics_mod.FaithfulnessMetric = _FakeMetric  # type: ignore[attr-defined]
    metrics_mod.AnswerRelevancyMetric = _FakeMetric  # type: ignore[attr-defined]
    test_case_mod = types.ModuleType("deepeval.test_case")
    test_case_mod.LLMTestCase = _FakeTestCase  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "deepeval", root)
    monkeypatch.setitem(sys.modules, "deepeval.metrics", metrics_mod)
    monkeypatch.setitem(sys.modules, "deepeval.test_case", test_case_mod)

    # Whatever an earlier test resolved must not leak in, and whatever this
    # test resolves must not leak out: monkeypatch restores all four.
    monkeypatch.setattr(metrics, "DEEPEVAL_AVAILABLE", False)
    for name in ("FaithfulnessMetric", "AnswerRelevancyMetric", "LLMTestCase"):
        monkeypatch.setattr(metrics, name, None, raising=False)


def test_enabling_after_import_builds_the_faithfulness_metric(
    fake_deepeval: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(evaluation_config, "enabled", True)

    evaluator = metrics.FaithfulnessEvaluator()
    score = evaluator.measure("q", "a", ["context"])

    assert isinstance(evaluator.metric, _FakeMetric)
    assert score == pytest.approx(0.9)


def test_enabling_after_import_builds_the_relevancy_metric(
    fake_deepeval: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(evaluation_config, "enabled", True)

    evaluator = metrics.AnswerRelevancyEvaluator()

    assert isinstance(evaluator.metric, _FakeMetric)
    assert evaluator.measure("q", "a") == pytest.approx(0.9)


def test_disabled_evaluation_still_skips_without_importing(
    fake_deepeval: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The opt-in must hold: disabled means no metric, whatever is installed."""
    monkeypatch.setattr(evaluation_config, "enabled", False)

    evaluator = metrics.FaithfulnessEvaluator()

    assert evaluator.metric is None
    assert metrics.DEEPEVAL_AVAILABLE is False


def test_missing_dependency_skips_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enabled but not installed degrades to a skip, as before."""
    monkeypatch.setattr(metrics, "DEEPEVAL_AVAILABLE", False)
    monkeypatch.setattr(evaluation_config, "enabled", True)
    # ``None`` in sys.modules makes the import raise ImportError.
    monkeypatch.setitem(sys.modules, "deepeval", None)
    monkeypatch.setitem(sys.modules, "deepeval.metrics", None)

    evaluator = metrics.FaithfulnessEvaluator()

    assert evaluator.metric is None
