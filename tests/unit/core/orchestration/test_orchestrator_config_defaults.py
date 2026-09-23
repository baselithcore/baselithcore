"""``ORCHESTRATOR_*`` settings reach the orchestrator and its classifier.

Regression: ``ORCHESTRATOR_DEFAULT_INTENT``, ``ORCHESTRATOR_CONFIDENCE_THRESHOLD``
and ``ORCHESTRATOR_ENABLE_TELEMETRY`` were declared but the orchestrator
hard-coded ``"qa_docs"``, the classifier ``0.6`` and telemetry off.
"""

from unittest.mock import MagicMock

import core.config.orchestration as orchestration_config
from core.config.orchestration import OrchestrationConfig
from core.orchestration.orchestrator import Orchestrator


def _orchestrator(monkeypatch, **fields) -> Orchestrator:
    monkeypatch.setattr(
        orchestration_config, "_orchestration_config", OrchestrationConfig(**fields)
    )
    return Orchestrator(plugin_registry=None, llm_service=MagicMock())


def test_settings_become_the_defaults(monkeypatch):
    orch = _orchestrator(
        monkeypatch,
        default_intent="general_chat",
        confidence_threshold=0.85,
        enable_telemetry=True,
    )
    assert orch.default_intent == "general_chat"
    assert orch.intent_classifier.default_intent == "general_chat"
    assert orch.intent_classifier.confidence_threshold == 0.85
    assert orch.intent_classifier.telemetry_enabled is True


def test_explicit_argument_wins(monkeypatch):
    monkeypatch.setattr(
        orchestration_config,
        "_orchestration_config",
        OrchestrationConfig(default_intent="general_chat"),
    )
    orch = Orchestrator(plugin_registry=None, default_intent="qa_docs")
    assert orch.default_intent == "qa_docs"
