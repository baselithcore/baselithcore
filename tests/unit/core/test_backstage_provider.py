"""
Unit tests for core.plugins.exporters.backstage_provider and protocols.

Coverage:
- BackstageExporter Protocol structural check
- to_catalog_info: field mapping, labels, annotations, spec
- detect_agentic_patterns: tag, resource, and source-scan strategies
- get_health_status: PluginState → lifecycle string
- Pattern cache: hit, invalidation
- _scan_source_files: import-grep logic
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List
from unittest.mock import MagicMock, patch

import pytest

from core.plugins.exporters.backstage_provider import (
    BackstageProvider,
    _scan_source_files,
    _slugify_title,
)
from core.plugins.lifecycle import PluginLifecycleManager, PluginState
from core.plugins.protocols import BackstageExporter, CatalogExporter


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_metadata(
    name: str = "test-plugin",
    version: str = "1.2.3",
    description: str = "A test plugin",
    author: str = "test-author",
    tags: List[str] | None = None,
    category: str = "AI",
    readiness: str = "stable",
    required_resources: List[str] | None = None,
    optional_resources: List[str] | None = None,
    homepage: str = "",
    license_: str = "",
    min_core_version: str | None = None,
    plugin_dependencies: Dict[str, str] | None = None,
):
    meta = MagicMock()
    meta.name = name
    meta.version = version
    meta.description = description
    meta.author = author
    meta.tags = tags or []
    meta.category = category
    meta.readiness = readiness
    meta.required_resources = required_resources or []
    meta.optional_resources = optional_resources or []
    meta.homepage = homepage
    meta.license = license_
    meta.min_core_version = min_core_version
    meta.plugin_dependencies = plugin_dependencies or {}
    return meta


def _make_plugin(
    name: str = "test-plugin",
    has_routers: bool = False,
    **meta_kwargs,
) -> MagicMock:
    plugin = MagicMock()
    plugin.metadata = _make_metadata(name=name, **meta_kwargs)
    plugin.get_routers.return_value = [MagicMock()] if has_routers else []
    return plugin


def _make_lifecycle(state_map: Dict[str, PluginState] | None = None) -> PluginLifecycleManager:
    lm = MagicMock(spec=PluginLifecycleManager)
    state_map = state_map or {}
    lm.get_state.side_effect = lambda name: state_map.get(name)
    return lm


def _provider(state_map: Dict[str, PluginState] | None = None) -> BackstageProvider:
    return BackstageProvider(
        lifecycle_manager=_make_lifecycle(state_map or {}),
        base_url="http://localhost:8000",
        docs_base_url="https://docs.example.com",
        catalog_source_location="url:https://github.com/org/repo/blob/main/",
    )


# ── Protocol checks ───────────────────────────────────────────────────────────


class TestProtocols:
    def test_backstage_provider_satisfies_catalog_exporter_protocol(self):
        p = _provider()
        assert isinstance(p, CatalogExporter)

    def test_backstage_provider_satisfies_backstage_exporter_protocol(self):
        p = _provider()
        assert isinstance(p, BackstageExporter)


# ── to_catalog_info ───────────────────────────────────────────────────────────


class TestToCatalogInfo:
    @pytest.fixture
    def plugin(self):
        return _make_plugin(
            name="my-plugin",
            version="2.0.0",
            description="Does something",
            author="alice",
            tags=["reasoning", "ai"],
            category="AI",
            readiness="beta",
            required_resources=["llm"],
            homepage="https://example.com",
            license_="MIT",
            min_core_version="0.3.0",
            plugin_dependencies={"dep-a": ">=1.0.0"},
        )

    @pytest.mark.asyncio
    async def test_apiversion_and_kind(self, plugin):
        entity = await _provider({"my-plugin": PluginState.ACTIVE}).to_catalog_info(plugin)
        assert entity["apiVersion"] == "backstage.io/v1alpha1"
        assert entity["kind"] == "Component"

    @pytest.mark.asyncio
    async def test_metadata_name_and_description(self, plugin):
        entity = await _provider().to_catalog_info(plugin)
        assert entity["metadata"]["name"] == "my-plugin"
        assert entity["metadata"]["description"] == "Does something"

    @pytest.mark.asyncio
    async def test_spec_owner_and_system(self, plugin):
        entity = await _provider().to_catalog_info(plugin)
        assert entity["spec"]["owner"] == "alice"
        assert entity["spec"]["system"] == "baselith-core"
        assert entity["spec"]["type"] == "baselith-plugin"

    @pytest.mark.asyncio
    async def test_lifecycle_maps_active_state(self, plugin):
        entity = await _provider({"my-plugin": PluginState.ACTIVE}).to_catalog_info(plugin)
        assert entity["spec"]["lifecycle"] == "production"

    @pytest.mark.asyncio
    async def test_lifecycle_maps_failed_state(self, plugin):
        entity = await _provider({"my-plugin": PluginState.FAILED}).to_catalog_info(plugin)
        assert entity["spec"]["lifecycle"] == "deprecated"

    @pytest.mark.asyncio
    async def test_lifecycle_unknown_for_missing_plugin(self, plugin):
        entity = await _provider({}).to_catalog_info(plugin)
        assert entity["spec"]["lifecycle"] == "unknown"

    @pytest.mark.asyncio
    async def test_provides_apis_when_has_routers(self):
        plugin = _make_plugin(name="router-plugin", has_routers=True)
        entity = await _provider().to_catalog_info(plugin)
        assert entity["spec"]["providesApis"] == ["router-plugin-api"]

    @pytest.mark.asyncio
    async def test_provides_apis_empty_when_no_routers(self, plugin):
        entity = await _provider().to_catalog_info(plugin)
        assert entity["spec"]["providesApis"] == []

    @pytest.mark.asyncio
    async def test_depends_on_populated(self, plugin):
        entity = await _provider().to_catalog_info(plugin)
        assert "component:dep-a" in entity["spec"]["dependsOn"]

    @pytest.mark.asyncio
    async def test_health_annotation_present(self, plugin):
        entity = await _provider().to_catalog_info(plugin)
        assert entity["metadata"]["annotations"]["baselith.ai/health-url"] == (
            "http://localhost:8000/health"
        )

    @pytest.mark.asyncio
    async def test_plugin_api_annotation_present(self, plugin):
        entity = await _provider().to_catalog_info(plugin)
        assert entity["metadata"]["annotations"]["baselith.ai/plugin-api-url"] == (
            "http://localhost:8000/api/plugins/my-plugin"
        )

    @pytest.mark.asyncio
    async def test_techdocs_annotation_present(self, plugin):
        entity = await _provider().to_catalog_info(plugin)
        assert entity["metadata"]["annotations"]["backstage.io/techdocs-ref"] == (
            "dir:./plugins/my-plugin"
        )

    @pytest.mark.asyncio
    async def test_homepage_annotation_when_set(self, plugin):
        entity = await _provider().to_catalog_info(plugin)
        assert (
            entity["metadata"]["annotations"]["backstage.io/source-location"]
            == "url:https://example.com"
        )

    @pytest.mark.asyncio
    async def test_no_homepage_annotation_when_empty(self):
        plugin = _make_plugin(name="no-home", homepage="")
        entity = await _provider().to_catalog_info(plugin)
        assert "backstage.io/source-location" not in entity["metadata"]["annotations"]

    @pytest.mark.asyncio
    async def test_category_tag_appended(self, plugin):
        entity = await _provider().to_catalog_info(plugin)
        assert "ai" in entity["metadata"]["tags"]

    @pytest.mark.asyncio
    async def test_readiness_label(self, plugin):
        entity = await _provider().to_catalog_info(plugin)
        assert entity["metadata"]["labels"]["baselith.ai/readiness"] == "beta"

    @pytest.mark.asyncio
    async def test_fallback_owner_when_no_author(self):
        plugin = _make_plugin(name="anon-plugin", author="")
        entity = await _provider().to_catalog_info(plugin)
        assert entity["spec"]["owner"] == "baselith-core-team"


# ── detect_agentic_patterns ───────────────────────────────────────────────────


class TestDetectAgenticPatterns:
    @pytest.mark.asyncio
    async def test_tag_detection(self):
        plugin = _make_plugin(tags=["reasoning", "reflection"])
        p = _provider()
        patterns = await p.detect_agentic_patterns(plugin)
        assert "baselith.ai/pattern-reasoning" in patterns
        assert "baselith.ai/pattern-reflection" in patterns

    @pytest.mark.asyncio
    async def test_resource_detection_llm(self):
        plugin = _make_plugin(required_resources=["llm"])
        p = _provider()
        patterns = await p.detect_agentic_patterns(plugin)
        assert "baselith.ai/pattern-reasoning" in patterns

    @pytest.mark.asyncio
    async def test_resource_detection_vectorstore(self):
        plugin = _make_plugin(optional_resources=["vectorstore"])
        p = _provider()
        patterns = await p.detect_agentic_patterns(plugin)
        assert "baselith.ai/pattern-memory-tiering" in patterns

    @pytest.mark.asyncio
    async def test_no_duplicate_patterns(self):
        # "llm" maps to reasoning, AND tag "reasoning" also maps to reasoning
        plugin = _make_plugin(tags=["reasoning"], required_resources=["llm"])
        p = _provider()
        patterns = await p.detect_agentic_patterns(plugin)
        assert patterns.count("baselith.ai/pattern-reasoning") == 1

    @pytest.mark.asyncio
    async def test_cache_hit_skips_recompute(self):
        plugin = _make_plugin(tags=["reasoning"])
        p = _provider()
        first = await p.detect_agentic_patterns(plugin)
        # Mutate the tag list — cache should still return the old result
        plugin.metadata.tags = []
        second = await p.detect_agentic_patterns(plugin)
        assert first == second

    @pytest.mark.asyncio
    async def test_invalidate_cache_forces_recompute(self):
        plugin = _make_plugin(tags=["reasoning"])
        p = _provider()
        await p.detect_agentic_patterns(plugin)
        plugin.metadata.tags = []
        p.invalidate_pattern_cache("test-plugin")
        second = await p.detect_agentic_patterns(plugin)
        assert "baselith.ai/pattern-reasoning" not in second

    @pytest.mark.asyncio
    async def test_source_scan_detects_import(self, tmp_path):
        (tmp_path / "agent.py").write_text(
            "from core.reflection import ReflectionAgent\n"
        )
        plugin = _make_plugin()
        # Point the module __file__ at the tmp_path
        mock_module = MagicMock()
        mock_module.__file__ = str(tmp_path / "plugin.py")
        p = _provider()
        with patch("inspect.getmodule", return_value=mock_module):
            patterns = await p.detect_agentic_patterns(plugin)
        assert "baselith.ai/pattern-reflection" in patterns


# ── get_health_status ─────────────────────────────────────────────────────────


class TestGetHealthStatus:
    @pytest.mark.asyncio
    async def test_active_returns_production(self):
        p = _provider({"my-plugin": PluginState.ACTIVE})
        assert await p.get_health_status("my-plugin") == "production"

    @pytest.mark.asyncio
    async def test_failed_returns_deprecated(self):
        p = _provider({"my-plugin": PluginState.FAILED})
        assert await p.get_health_status("my-plugin") == "deprecated"

    @pytest.mark.asyncio
    async def test_loading_returns_experimental(self):
        p = _provider({"my-plugin": PluginState.LOADING})
        assert await p.get_health_status("my-plugin") == "experimental"

    @pytest.mark.asyncio
    async def test_missing_plugin_returns_unknown(self):
        p = _provider({})
        assert await p.get_health_status("ghost") == "unknown"


# ── _scan_source_files ────────────────────────────────────────────────────────


class TestScanSourceFiles:
    def test_detects_from_import(self, tmp_path):
        (tmp_path / "agent.py").write_text(
            "from core.reasoning.tot import TreeOfThoughts\n"
        )
        found = _scan_source_files(tmp_path)
        assert "baselith.ai/pattern-reasoning" in found

    def test_detects_import_statement(self, tmp_path):
        (tmp_path / "plugin.py").write_text("import core.swarm\n")
        found = _scan_source_files(tmp_path)
        assert "baselith.ai/pattern-swarm" in found

    def test_no_false_positives(self, tmp_path):
        (tmp_path / "plugin.py").write_text("import os\nimport sys\n")
        found = _scan_source_files(tmp_path)
        assert found == []

    def test_no_duplicates_across_files(self, tmp_path):
        (tmp_path / "a.py").write_text("from core.reasoning import X\n")
        (tmp_path / "b.py").write_text("from core.reasoning import Y\n")
        found = _scan_source_files(tmp_path)
        assert found.count("baselith.ai/pattern-reasoning") == 1

    def test_empty_directory_returns_empty_list(self, tmp_path):
        assert _scan_source_files(tmp_path) == []

    def test_nonexistent_directory_returns_empty_list(self):
        assert _scan_source_files(Path("/nonexistent/path/xyz")) == []


# ── _slugify_title ────────────────────────────────────────────────────────────


class TestSlugifyTitle:
    def test_kebab_to_title(self):
        assert _slugify_title("my-reasoning-agent") == "My Reasoning Agent"

    def test_snake_to_title(self):
        assert _slugify_title("my_reasoning_agent") == "My Reasoning Agent"

    def test_already_clean(self):
        assert _slugify_title("reasoning") == "Reasoning"


# ── export_all / get_provider_payload ─────────────────────────────────────────


class TestExportAll:
    @pytest.mark.asyncio
    async def test_export_all_returns_one_entity_per_plugin(self):
        registry = MagicMock()
        registry.get_all.return_value = [
            _make_plugin("plugin-a"),
            _make_plugin("plugin-b"),
        ]
        p = _provider({"plugin-a": PluginState.ACTIVE, "plugin-b": PluginState.ACTIVE})
        entities = await p.export_all(registry)
        assert len(entities) == 2
        names = {e["metadata"]["name"] for e in entities}
        assert names == {"plugin-a", "plugin-b"}

    @pytest.mark.asyncio
    async def test_get_provider_payload_type_is_full(self):
        registry = MagicMock()
        registry.get_all.return_value = [_make_plugin("only-plugin")]
        p = _provider()
        payload = await p.get_provider_payload(registry)
        assert payload["type"] == "full"
        assert len(payload["entities"]) == 1
