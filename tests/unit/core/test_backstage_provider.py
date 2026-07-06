"""
Unit tests for core.plugins.exporters.backstage_provider and protocols.

Coverage:
- BackstageExporter Protocol structural check
- to_catalog_info: field mapping, labels, annotations, spec
- detect_agentic_patterns: tag, resource, and source-scan strategies
- get_health_status: PluginState → runtime lifecycle string
- Pattern cache: hit, invalidation
- _scan_source_files: import-grep logic

Graph-level coverage (export_all / export_graph / entity_model) lives in
test_backstage_graph.py; shared builders in backstage_test_utils.py.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from backstage_test_utils import (
    make_lifecycle as _make_lifecycle,  # noqa: F401 - re-exported for parity
)
from backstage_test_utils import (
    make_plugin as _make_plugin,
)
from backstage_test_utils import (
    make_provider as _provider,
)

from core.plugins.exporters.backstage_provider import (
    _scan_source_files,
    _slugify_title,
)
from core.plugins.lifecycle import PluginState
from core.plugins.protocols import BackstageExporter, CatalogExporter

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
        entity = await _provider({"my-plugin": PluginState.ACTIVE}).to_catalog_info(
            plugin
        )
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
        assert entity["spec"]["owner"] == "group:default/alice"
        assert entity["spec"]["system"] == "baselith-core"
        assert entity["spec"]["type"] == "baselith-plugin"

    @pytest.mark.asyncio
    async def test_lifecycle_derives_from_readiness_not_state(self, plugin):
        # readiness="beta" → experimental, even while the plugin runs ACTIVE:
        # spec.lifecycle is maturity, runtime health lives in a label.
        entity = await _provider({"my-plugin": PluginState.ACTIVE}).to_catalog_info(
            plugin
        )
        assert entity["spec"]["lifecycle"] == "experimental"
        assert entity["metadata"]["labels"]["baselith.ai/runtime-state"] == "active"

    @pytest.mark.asyncio
    async def test_stable_readiness_maps_to_production(self):
        plugin = _make_plugin(name="stable-plugin", readiness="stable")
        entity = await _provider({"stable-plugin": PluginState.FAILED}).to_catalog_info(
            plugin
        )
        assert entity["spec"]["lifecycle"] == "production"
        assert entity["metadata"]["labels"]["baselith.ai/runtime-state"] == "failed"

    @pytest.mark.asyncio
    async def test_deprecated_readiness_maps_to_deprecated(self):
        plugin = _make_plugin(name="old-plugin", readiness="deprecated")
        entity = await _provider().to_catalog_info(plugin)
        assert entity["spec"]["lifecycle"] == "deprecated"

    @pytest.mark.asyncio
    async def test_runtime_state_unknown_for_missing_plugin(self, plugin):
        entity = await _provider({}).to_catalog_info(plugin)
        assert entity["metadata"]["labels"]["baselith.ai/runtime-state"] == "unknown"

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
        assert "component:default/dep-a" in entity["spec"]["dependsOn"]

    @pytest.mark.asyncio
    async def test_required_resources_become_resource_dependencies(self, plugin):
        # fixture declares required_resources=["llm"]
        entity = await _provider().to_catalog_info(plugin)
        assert "resource:default/llm" in entity["spec"]["dependsOn"]

    @pytest.mark.asyncio
    async def test_optional_resources_exported_as_annotation(self):
        plugin = _make_plugin(name="opt", optional_resources=["redis", "qdrant"])
        entity = await _provider().to_catalog_info(plugin)
        assert (
            entity["metadata"]["annotations"]["baselith.ai/optional-resources"]
            == "qdrant,redis"
        )
        # Optional infra resources also become dependency edges so the
        # "Depends on resources" card is populated (optionality is kept in the
        # annotation above).
        assert "resource:default/redis" in entity["spec"]["dependsOn"]
        assert "resource:default/qdrant" in entity["spec"]["dependsOn"]

    @pytest.mark.asyncio
    async def test_namespace_and_plugin_id_annotation(self, plugin):
        entity = await _provider().to_catalog_info(plugin)
        assert entity["metadata"]["namespace"] == "default"
        assert entity["metadata"]["annotations"]["baselith.ai/plugin-id"] == "my-plugin"

    @pytest.mark.asyncio
    async def test_managed_by_location_annotations(self, plugin):
        entity = await _provider().to_catalog_info(plugin)
        ann = entity["metadata"]["annotations"]
        expected = "url:http://localhost:8000/api/backstage/entities"
        assert ann["backstage.io/managed-by-location"] == expected
        assert ann["backstage.io/managed-by-origin-location"] == expected

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
    async def test_techdocs_annotation_absent_without_mkdocs(self, plugin):
        # No mkdocs.yml in the (mocked) plugin dir → no broken Docs tab.
        entity = await _provider().to_catalog_info(plugin)
        assert "backstage.io/techdocs-ref" not in entity["metadata"]["annotations"]

    @pytest.mark.asyncio
    async def test_techdocs_annotation_present_with_mkdocs(self, plugin, tmp_path):
        (tmp_path / "mkdocs.yml").write_text("site_name: docs\n")
        mock_module = MagicMock()
        mock_module.__file__ = str(tmp_path / "plugin.py")
        with patch("inspect.getmodule", return_value=mock_module):
            entity = await _provider().to_catalog_info(plugin)
        assert entity["metadata"]["annotations"]["backstage.io/techdocs-ref"] == (
            "url:https://github.com/org/repo/blob/main/plugins/my-plugin"
        )

    @pytest.mark.asyncio
    async def test_source_location_points_at_repo_plugin_dir(self, plugin):
        # source-location is always the plugin's directory in the repository —
        # the homepage (if any) is exposed as a link, not as source-location.
        entity = await _provider().to_catalog_info(plugin)
        assert (
            entity["metadata"]["annotations"]["backstage.io/source-location"]
            == "url:https://github.com/org/repo/blob/main/plugins/my-plugin/"
        )
        assert {
            "url": "https://example.com",
            "title": "Homepage",
            "icon": "web",
        } in entity["metadata"]["links"]

    @pytest.mark.asyncio
    async def test_links_are_browser_renderable_only(self, plugin):
        # Machine endpoints (plugin admin API) must NOT appear as links —
        # they answer 401 JSON in a browser. They stay in annotations.
        entity = await _provider().to_catalog_info(plugin)
        titles = {link["title"] for link in entity["metadata"]["links"]}
        assert "Plugin API" not in titles
        assert (
            entity["metadata"]["annotations"]["baselith.ai/plugin-api-url"]
            == "http://localhost:8000/api/plugins/my-plugin"
        )
        # Docs site configured in the fixture provider → link present.
        assert "Documentation" in titles

    @pytest.mark.asyncio
    async def test_docs_link_omitted_when_unconfigured(self, plugin):
        entity = await _provider(docs_base_url=None).to_catalog_info(plugin)
        titles = {link["title"] for link in entity["metadata"]["links"]}
        assert "Documentation" not in titles

    @pytest.mark.asyncio
    async def test_manage_link_from_template(self, plugin):
        provider = _provider(
            plugin_link_template="http://h:8000/baselithcontrol/#/plugin/{plugin}"
        )
        entity = await provider.to_catalog_info(plugin)
        assert {
            "url": "http://h:8000/baselithcontrol/#/plugin/my-plugin",
            "title": "Manage Plugin",
            "icon": "dashboard",
        } in entity["metadata"]["links"]

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
        assert entity["spec"]["owner"] == "group:default/baselithcore-team"

    @pytest.mark.asyncio
    async def test_author_with_email_becomes_valid_owner_ref(self):
        plugin = _make_plugin(name="p", author="Jane Doe <jane@corp.io>")
        entity = await _provider().to_catalog_info(plugin)
        assert entity["spec"]["owner"] == "group:default/jane-doe"

    @pytest.mark.asyncio
    async def test_invalid_plugin_name_is_sanitised(self):
        plugin = _make_plugin(name="My Plugin!!")
        entity = await _provider().to_catalog_info(plugin)
        assert entity["metadata"]["name"] == "My-Plugin"
        # The raw registry identity survives in the plugin-id annotation.
        assert (
            entity["metadata"]["annotations"]["baselith.ai/plugin-id"] == "My Plugin!!"
        )


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


# ── Path safety: manifest names must never traverse annotation paths ─────────


class TestAnnotationPathSafety:
    def test_file_location_annotations_sanitizes_traversal(self):
        from core.plugins.exporters.entity_model import file_location_annotations

        ann = file_location_annotations("/srv/repo", "../../etc/passwd")
        loc = ann["backstage.io/managed-by-location"]
        assert loc == "file:/srv/repo/plugins/etc-passwd/manifest.yaml"
        assert "../" not in loc

    def test_file_location_annotations_keeps_legit_names(self):
        from core.plugins.exporters.entity_model import file_location_annotations

        ann = file_location_annotations("/srv/repo/", "coding_agent")
        assert (
            ann["backstage.io/managed-by-origin-location"]
            == "file:/srv/repo/plugins/coding_agent/manifest.yaml"
        )
