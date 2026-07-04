"""Shared fixtures/builders for the Backstage exporter test modules.

Not a test module (no ``test_`` prefix): pytest leaves it uncollected, and the
sibling test files import it via the rootdir-inserted ``sys.path`` entry.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from core.plugins.exporters.backstage_provider import BackstageProvider
from core.plugins.lifecycle import PluginLifecycleManager, PluginState


def make_metadata(
    name: str = "test-plugin",
    version: str = "1.2.3",
    description: str = "A test plugin",
    author: str = "test-author",
    tags: list[str] | None = None,
    category: str = "AI",
    readiness: str = "stable",
    required_resources: list[str] | None = None,
    optional_resources: list[str] | None = None,
    homepage: str = "",
    license_: str = "",
    min_core_version: str | None = None,
    plugin_dependencies: dict[str, str] | None = None,
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


def make_plugin(
    name: str = "test-plugin",
    has_routers: bool = False,
    router_prefix: str = "",
    **meta_kwargs,
) -> MagicMock:
    plugin = MagicMock()
    plugin.metadata = make_metadata(name=name, **meta_kwargs)
    if has_routers:
        router = MagicMock()
        router.prefix = router_prefix
        router.routes = []
        plugin.get_routers.return_value = [router]
    else:
        plugin.get_routers.return_value = []
    plugin.get_router_prefix.return_value = f"/api/{name}"
    return plugin


def make_lifecycle(
    state_map: dict[str, PluginState] | None = None,
) -> PluginLifecycleManager:
    lm = MagicMock(spec=PluginLifecycleManager)
    state_map = state_map or {}
    lm.get_state.side_effect = lambda name: state_map.get(name)
    return lm


def make_provider(
    state_map: dict[str, PluginState] | None = None, **kwargs
) -> BackstageProvider:
    return BackstageProvider(
        lifecycle_manager=make_lifecycle(state_map or {}),
        base_url="http://localhost:8000",
        docs_base_url="https://docs.example.com",
        catalog_source_location="url:https://github.com/org/repo/blob/main/",
        **kwargs,
    )
