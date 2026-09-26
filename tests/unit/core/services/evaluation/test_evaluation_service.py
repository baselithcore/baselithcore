"""The retired ``core.services.evaluation`` shim."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

import core.services.evaluation as shim
from core.evaluation import EvaluationService as LiveEvaluationService


def test_shim_is_deprecated_alias_of_live_service() -> None:
    with pytest.warns(DeprecationWarning, match="core.evaluation.EvaluationService"):
        service = shim.EvaluationService(event_bus=MagicMock())
    assert isinstance(service, LiveEvaluationService)


def test_get_evaluation_service_warns_and_is_cached(monkeypatch) -> None:
    monkeypatch.setattr(shim, "_eval_service", None)
    with (
        patch("core.evaluation.service.get_event_bus", return_value=MagicMock()),
        pytest.warns(DeprecationWarning),
    ):
        first = shim.get_evaluation_service()
        second = shim.get_evaluation_service()
    assert first is second


def test_no_credential_is_exported(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.warns(DeprecationWarning):
        shim.EvaluationService(event_bus=MagicMock(), use_openai=True)
    assert "OPENAI_API_KEY" not in os.environ


async def test_evaluate_rag_uses_fresh_metrics_per_call() -> None:
    built: list[MagicMock] = []

    def make(kind: str, score: float):
        def factory(*_a, **_k):
            ev = MagicMock()
            ev.threshold = 0.7
            ev.metric = MagicMock(reason=f"{kind}-reason")
            ev.measure.return_value = score
            built.append(ev)
            return ev

        return factory

    with (
        patch("core.evaluation.metrics.FaithfulnessEvaluator", make("f", 0.9)),
        patch("core.evaluation.metrics.AnswerRelevancyEvaluator", make("r", 0.5)),
        pytest.warns(DeprecationWarning),
    ):
        service = shim.EvaluationService(event_bus=MagicMock())
        first = await service.evaluate_rag_response("q", "a", ["ctx"])
        await service.evaluate_rag_response("q", "a", ["ctx"])

    assert len(built) == 4  # two evaluators per call, none shared
    assert first["faithfulness"] == {"score": 0.9, "reason": "f-reason", "passed": True}
    assert first["answer_relevancy"]["passed"] is False


async def test_evaluate_rag_reports_unavailable_without_metric() -> None:
    ev = MagicMock(threshold=0.7, metric=None)
    ev.measure.return_value = 0.0
    with (
        patch("core.evaluation.metrics.FaithfulnessEvaluator", return_value=ev),
        patch("core.evaluation.metrics.AnswerRelevancyEvaluator", return_value=ev),
        pytest.warns(DeprecationWarning),
    ):
        service = shim.EvaluationService(event_bus=MagicMock())
        result = await service.evaluate_rag_response("q", "a", [])
    assert "error" in result["faithfulness"]
