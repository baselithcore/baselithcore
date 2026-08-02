"""Tests for the registry-backed conversation system prompt (prompt-as-code)."""

from __future__ import annotations

import pytest

import core.prompts.registry as registry_module
from core.chat.prompt import (
    _CATALOG_FILE,
    _SYSTEM_PROMPT_PREFIX,
    _SYSTEM_PROMPT_SUFFIX,
    CONVERSATION_PROMPT_NAME,
    CONVERSATION_SYSTEM_PROMPT,
    _system_prompt,
    build_prompt,
)
from core.prompts.loader import parse_prompt_file


@pytest.fixture(autouse=True)
def _fresh_registry(monkeypatch):
    """Isolate the global prompt registry per test."""
    monkeypatch.setattr(registry_module, "_registry", None)
    yield
    monkeypatch.setattr(registry_module, "_registry", None)


class TestCatalogFile:
    def test_catalog_body_matches_embedded_default(self):
        """The .md catalog and the embedded fallback constant must not drift."""
        pv = parse_prompt_file(_CATALOG_FILE)
        expected = CONVERSATION_SYSTEM_PROMPT.replace(
            "{current_date}", "{{ current_date }}"
        )
        assert pv.template == expected
        assert pv.name == CONVERSATION_PROMPT_NAME
        assert "production" in pv.labels
        assert pv.variables == ["current_date"]


class TestSystemPromptResolution:
    def test_registry_render_is_byte_identical_to_legacy(self):
        legacy = f"{_SYSTEM_PROMPT_PREFIX}01/08/2026{_SYSTEM_PROMPT_SUFFIX}"
        assert _system_prompt("01/08/2026") == legacy

    def test_deployment_override_wins(self):
        registry = registry_module.get_prompt_registry()
        registry.register(
            CONVERSATION_PROMPT_NAME,
            "OVERRIDDEN for {{ current_date }}",
            version="2",
            labels={"production"},
        )
        assert _system_prompt("today") == "OVERRIDDEN for today"

    def test_render_emits_provenance(self):
        _system_prompt("01/08/2026")
        registry = registry_module.get_prompt_registry()
        pv = registry.get(CONVERSATION_PROMPT_NAME, label="production")
        assert pv.checksum  # content-addressed identity available for spans


class TestBuildPrompt:
    def test_build_prompt_contains_rendered_system_prompt(self):
        out = build_prompt("what?", "ctx text", "")
        assert "AI Assistant – System Prompt" in out
        assert "### CONTEXT:\nctx text" in out
        assert "### QUESTION:\nwhat?" in out

    def test_build_prompt_sections_order_preserved(self):
        out = build_prompt("q", "c", "older turn", additional_context="plugin ctx")
        assert out.index("PREVIOUS CONVERSATION") < out.index("plugin ctx")
        assert out.index("plugin ctx") < out.index("### CONTEXT:")
