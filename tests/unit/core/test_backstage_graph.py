"""
Graph-level tests for the Backstage exporter: export_all, export_graph
(Domain + System + Groups + Resources + Components + APIs, inline OpenAPI
definitions) and the entity_model format helpers.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from backstage_test_utils import (
    make_lifecycle as _make_lifecycle,
)
from backstage_test_utils import (
    make_plugin as _make_plugin,
)
from backstage_test_utils import (
    make_provider as _provider,
)

from core.plugins.exporters import entity_model as em
from core.plugins.exporters.backstage_provider import BackstageProvider
from core.plugins.lifecycle import PluginState

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
        kinds = [e["kind"] for e in payload["entities"]]
        # Full graph: Domain + System roots, the owner Groups (platform team +
        # manifest author), the Resource behind required_resources is absent
        # here (fixture declares none), then the plugin Component (no routers
        # → no API entity).
        assert kinds == ["Domain", "System", "Group", "Group", "Component"]


# ── export_graph ──────────────────────────────────────────────────────────────


class TestExportGraph:
    @pytest.mark.asyncio
    async def test_graph_has_no_dangling_references(self):
        registry = MagicMock()
        registry.get_all.return_value = [
            _make_plugin("with-api", has_routers=True),
            _make_plugin("bare"),
        ]
        p = _provider()
        graph = await p.export_graph(registry)

        emitted = {
            f"{e['kind'].lower()}:default/{e['metadata']['name']}" for e in graph
        }
        systems = [e for e in graph if e["kind"] == "System"]
        assert len(systems) == 1 and systems[0]["metadata"]["name"] == "baselith-core"

        system_names = {s["metadata"]["name"] for s in systems}
        for component in (e for e in graph if e["kind"] == "Component"):
            assert component["spec"]["system"] in system_names
            # Owner Groups, provided APIs, and Resource dependencies must all
            # resolve inside the same payload — no dangling refs.
            assert component["spec"]["owner"] in emitted
            for provided in component["spec"]["providesApis"]:
                assert f"api:default/{provided}" in emitted
            for dep in component["spec"]["dependsOn"]:
                if dep.startswith("resource:"):
                    assert dep in emitted

    @pytest.mark.asyncio
    async def test_domain_system_and_resources_emitted(self):
        registry = MagicMock()
        registry.get_all.return_value = [
            _make_plugin("db-plugin", required_resources=["postgres", "llm"]),
        ]
        graph = await _provider().export_graph(registry)
        domain = next(e for e in graph if e["kind"] == "Domain")
        system = next(e for e in graph if e["kind"] == "System")
        assert domain["metadata"]["name"] == "baselith"
        assert system["spec"]["domain"] == "baselith"

        resources = {e["metadata"]["name"]: e for e in graph if e["kind"] == "Resource"}
        assert set(resources) == {"postgres", "llm"}
        assert resources["postgres"]["spec"]["type"] == "database"
        assert resources["llm"]["spec"]["type"] == "llm-provider"

    @pytest.mark.asyncio
    async def test_api_entity_mirrors_component(self):
        registry = MagicMock()
        registry.get_all.return_value = [
            _make_plugin("router-plugin", has_routers=True, readiness="stable")
        ]
        p = _provider({"router-plugin": PluginState.ACTIVE})
        graph = await p.export_graph(registry)
        api = next(e for e in graph if e["kind"] == "API")
        assert api["metadata"]["name"] == "router-plugin-api"
        assert api["spec"]["type"] == "openapi"
        assert api["spec"]["lifecycle"] == "production"
        assert api["spec"]["system"] == "baselith-core"
        # Without an openapi supplier the definition falls back to $text.
        assert api["spec"]["definition"] == {
            "$text": "http://localhost:8000/openapi.json"
        }

    @pytest.mark.asyncio
    async def test_api_definition_inlined_when_supplier_available(self):
        openapi_doc = {
            "openapi": "3.1.0",
            "info": {"title": "BaselithCore", "version": "1.0.0"},
            "paths": {
                "/api/router-plugin/items": {
                    "get": {
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {"$ref": "#/components/schemas/Item"}
                                    }
                                }
                            }
                        }
                    }
                },
                "/api/other/route": {"get": {}},
            },
            "components": {
                "schemas": {
                    "Item": {"$ref": "#/components/schemas/Nested"},
                    "Nested": {"type": "object"},
                    "Unrelated": {"type": "string"},
                }
            },
        }
        provider = BackstageProvider(
            lifecycle_manager=_make_lifecycle({}),
            base_url="http://localhost:8000",
            docs_base_url="https://docs.example.com",
            catalog_source_location="url:https://github.com/org/repo/blob/main/",
            openapi_supplier=lambda: openapi_doc,
        )
        registry = MagicMock()
        registry.get_all.return_value = [
            _make_plugin("router-plugin", has_routers=True)
        ]
        graph = await provider.export_graph(registry)
        api = next(e for e in graph if e["kind"] == "API")

        import json

        definition = json.loads(api["spec"]["definition"])
        assert set(definition["paths"]) == {"/api/router-plugin/items"}
        # $ref pruning is transitive; unreferenced schemas are dropped.
        assert set(definition["components"]["schemas"]) == {"Item", "Nested"}

    @pytest.mark.asyncio
    async def test_broad_base_prefix_scoped_by_router_prefix(self):
        # auth-like layout: get_router_prefix()="/api", router.prefix="/auth".
        # The definition must be scoped to /api/auth — NOT swallow every /api
        # route; a bare "/api" prefix alone is rejected (→ $text fallback).
        openapi_doc = {
            "openapi": "3.1.0",
            "info": {"title": "t", "version": "1"},
            "paths": {
                "/api/auth/login": {"post": {}},
                "/api/other/route": {"get": {}},
            },
        }
        provider = BackstageProvider(
            lifecycle_manager=_make_lifecycle({}),
            openapi_supplier=lambda: openapi_doc,
        )
        plugin = _make_plugin("auth-like", has_routers=True, router_prefix="/auth")
        plugin.get_router_prefix.return_value = "/api"

        import json

        raw = provider.build_api_definition(plugin)
        assert raw is not None
        definition = json.loads(raw)
        assert set(definition["paths"]) == {"/api/auth/login"}

        # Same layout but no router prefix → only "/api" remains → None.
        bare = _make_plugin("bare-api", has_routers=True, router_prefix="")
        bare.get_router_prefix.return_value = "/api"
        assert provider.build_api_definition(bare) is None

    @pytest.mark.asyncio
    async def test_combined_router_scoped_by_real_route_paths(self):
        # auth's actual layout: a combined APIRouter with prefix "" mounted at
        # /api, whose .routes already carry the sub-router prefixes. The
        # definition must be selected from the real route paths (converter
        # suffixes like {p:path} normalised away), not from the broad prefix.
        openapi_doc = {
            "openapi": "3.1.0",
            "info": {"title": "t", "version": "1"},
            "paths": {
                "/api/auth/login": {"post": {}},
                "/api/proxy/{path}": {"get": {}},
                "/api/other/route": {"get": {}},
            },
        }
        provider = BackstageProvider(
            lifecycle_manager=_make_lifecycle({}),
            openapi_supplier=lambda: openapi_doc,
        )
        plugin = _make_plugin("combined", has_routers=True, router_prefix="")
        plugin.get_router_prefix.return_value = "/api"
        login = MagicMock()
        login.path = "/auth/login"
        catchall = MagicMock()
        catchall.path = "/proxy/{path:path}"
        plugin.get_routers.return_value[0].routes = [login, catchall]

        import json

        raw = provider.build_api_definition(plugin)
        assert raw is not None
        definition = json.loads(raw)
        assert set(definition["paths"]) == {"/api/auth/login", "/api/proxy/{path}"}

    @pytest.mark.asyncio
    async def test_every_graph_entity_carries_location_annotations(self):
        registry = MagicMock()
        registry.get_all.return_value = [_make_plugin("p", has_routers=True)]
        graph = await _provider().export_graph(registry)
        for entity in graph:
            ann = entity["metadata"]["annotations"]
            assert "backstage.io/managed-by-location" in ann
            assert "backstage.io/managed-by-origin-location" in ann


# ── entity_model helpers ──────────────────────────────────────────────────────


class TestEntityModel:
    def test_sanitize_entity_name_strips_invalid_chars(self):
        assert em.sanitize_entity_name("My Plugin!!") == "My-Plugin"
        assert em.sanitize_entity_name("  ") == "unknown"
        assert em.sanitize_entity_name(None) == "unknown"
        assert len(em.sanitize_entity_name("x" * 100)) == 63

    def test_sanitize_label_value_edges_alphanumeric(self):
        assert em.sanitize_label_value("-beta-") == "beta"
        assert em.sanitize_label_value("") == "unknown"
        assert em.sanitize_label_value("1.2.3") == "1.2.3"

    def test_owner_ref_variants(self):
        assert em.owner_ref("alice") == "group:default/alice"
        assert em.owner_ref("Jane Doe <jane@x.io>") == "group:default/jane-doe"
        assert em.owner_ref("") == "group:default/baselithcore-team"
        assert em.owner_ref(None) == "group:default/baselithcore-team"

    def test_api_name_truncates_to_valid_length(self):
        name = em.api_name("p" * 100)
        assert name.endswith("-api")
        assert len(name) <= 63

    def test_component_ref(self):
        assert em.component_ref("auth") == "component:default/auth"


# ── sub-app-mount API entities ─────────────────────────────────────────────────


def _mount_route(name: str, path: str, paths: dict):
    """A minimal Starlette-Mount stand-in exposing a FastAPI-style openapi()."""
    sub_app = MagicMock()
    sub_app.openapi.return_value = {
        "openapi": "3.1.0",
        "info": {"title": name, "version": "1.0.0"},
        "paths": paths,
    }
    route = MagicMock()
    route.app = sub_app
    route.name = name
    route.path = path
    return route


class TestSubAppMountApis:
    @pytest.mark.asyncio
    async def test_mounted_subapp_gets_api_entity(self):
        registry = MagicMock()
        registry.get_all.return_value = [_make_plugin("wikigen")]  # no host routers
        p = _provider({"wikigen": PluginState.ACTIVE})
        route = _mount_route(
            "wikigen", "/wikigen", {"/api/pages": {"get": {"responses": {}}}}
        )

        graph = await p.export_graph(registry, routes=[route])

        comp = next(e for e in graph if e["kind"] == "Component")
        assert comp["spec"]["providesApis"] == [em.api_name("wikigen")]

        apis = [e for e in graph if e["kind"] == "API"]
        assert len(apis) == 1 and apis[0]["metadata"]["name"] == em.api_name("wikigen")
        import json

        definition = json.loads(apis[0]["spec"]["definition"])
        # Paths are re-prefixed with the mount path so they stay addressable.
        assert "/wikigen/api/pages" in definition["paths"]

    @pytest.mark.asyncio
    async def test_no_routes_means_no_subapp_api(self):
        registry = MagicMock()
        registry.get_all.return_value = [_make_plugin("wikigen")]
        p = _provider({"wikigen": PluginState.ACTIVE})

        graph = await p.export_graph(registry)  # routes=None → no discovery

        assert not [e for e in graph if e["kind"] == "API"]
        comp = next(e for e in graph if e["kind"] == "Component")
        assert comp["spec"]["providesApis"] == []

    @pytest.mark.asyncio
    async def test_staticfiles_mount_is_ignored(self):
        registry = MagicMock()
        registry.get_all.return_value = [_make_plugin("aura")]
        p = _provider({"aura": PluginState.ACTIVE})
        # A StaticFiles/SPA mount has no callable openapi() → no API entity.
        static = MagicMock()
        static.app = MagicMock(spec=[])  # no openapi attribute
        static.name = "aura"
        static.path = "/aura"

        graph = await p.export_graph(registry, routes=[static])

        assert not [e for e in graph if e["kind"] == "API"]
