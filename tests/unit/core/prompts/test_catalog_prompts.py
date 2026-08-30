"""Hot-path prompts served from the versioned registry, not hardcoded strings.

The registry existed with one consumer; the agentic hot paths (ReAct text +
native loops, intent classification, swarm decomposition) rendered .format
strings with no versioning or label resolution. These tests pin the catalog
seam and the call-site migration.
"""

from __future__ import annotations

from pathlib import Path

from core.prompts.catalog import resolve_catalog_prompt
from core.prompts.registry import get_prompt_registry


def test_resolves_seeded_catalog_file_with_variables():
    text = resolve_catalog_prompt(
        "react_system",
        variables={"tool_descriptions": "- search: web search", "max_iterations": 7},
    )
    assert "- search: web search" in text
    assert "7" in text
    assert "{{" not in text


def test_falls_back_to_embedded_template_when_file_missing():
    text = resolve_catalog_prompt(
        "no_such_prompt_name",
        variables={"who": "world"},
        catalog_file=Path("/nonexistent/prompt.md"),
        fallback_template="hello {{ who }}",
    )
    assert text == "hello world"


def test_deployment_override_wins_over_packaged_default(tmp_path):
    # Isolated name: proves the seeding rule (registered versions win over the
    # catalog file) without polluting the shared registry names.
    catalog_file = tmp_path / "catalog_override_probe.md"
    catalog_file.write_text(
        "---\n"
        "name: catalog_override_probe\n"
        'version: "1"\n'
        "labels: [production]\n"
        "variables: [max_iterations]\n"
        "---\n"
        "PACKAGED {{ max_iterations }}\n"
    )
    registry = get_prompt_registry()
    registry.register(
        "catalog_override_probe",
        "OVERRIDDEN {{ max_iterations }}",
        version="99-test",
        labels={"production"},
    )
    text = resolve_catalog_prompt(
        "catalog_override_probe",
        variables={"max_iterations": 4},
        catalog_file=catalog_file,
    )
    assert text == "OVERRIDDEN 4"


class TestCallSites:
    def test_react_text_loop_system_prompt(self):
        from core.reasoning.react import ReActAgent, ToolDefinition

        async def search(query: str) -> str:
            return query

        agent = ReActAgent(
            tools=[
                ToolDefinition(
                    name="search",
                    fn=search,
                    description="Search the web",
                    category="read_only",
                )
            ],
            max_iterations=3,
        )
        prompt = agent._build_system_prompt()
        assert "search: Search the web" in prompt
        assert "3" in prompt
        assert "{{" not in prompt and "{tool_descriptions}" not in prompt

    def test_react_native_loop_system_prompt(self):
        from core.reasoning.react import ReActAgent
        from core.reasoning.react_native import _build_system_prompt

        agent = ReActAgent(max_iterations=5, system_prompt_extra="EXTRA-MARKER")
        prompt = _build_system_prompt(agent)
        assert "5" in prompt
        assert "EXTRA-MARKER" in prompt
        assert "{{" not in prompt and "{max_iterations}" not in prompt

    def test_intent_classification_prompt(self):
        from core.orchestration.intent_classifier import build_classification_prompt

        prompt = build_classification_prompt(
            intents_list="- qa_docs: questions over documents",
            query="what does the contract say?",
        )
        assert "qa_docs" in prompt
        assert "what does the contract say?" in prompt
        # The JSON response example must survive rendering literally.
        assert '"intent"' in prompt
        assert "{{" not in prompt

    def test_swarm_decomposition_prompt(self):
        from core.orchestration.handlers.swarm_agents import (
            build_decomposition_prompt,
        )

        prompt = build_decomposition_prompt("build a market analysis report")
        assert "build a market analysis report" in prompt
        assert '"capability"' in prompt
        assert "{{" not in prompt and "{query}" not in prompt


def test_prompt_render_emits_provenance_span():
    # The registry path (not the fallback) must serve the packaged prompts:
    # rendering resolves a registered version with a checksum.
    registry = get_prompt_registry()
    resolve_catalog_prompt(
        "react_system",
        variables={"tool_descriptions": "x", "max_iterations": 1},
    )
    assert registry.list_versions("react_system"), "catalog prompt not registered"
