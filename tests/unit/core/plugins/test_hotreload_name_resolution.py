"""Hot-reload addresses a plugin by directory *or* manifest name.

The loader keys lifecycle state, the registry and its module cache by the
manifest name. Several shipped plugins live in a directory whose name differs
(``coding_agent`` vs ``coding-agent``), and the admin routes pass whatever the
operator typed. Before the fix a directory-named disable failed with "state
None", and a second directory-named enable loaded a duplicate instance and left
the live one stuck in INITIALIZING.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from core.plugins import PluginLoader, PluginRegistry
from core.plugins.hotreload import HotReloadController
from core.plugins.lifecycle import PluginLifecycleManager, PluginState

pytestmark = [pytest.mark.unit]

DIR_NAME = "fancy_plugin"
MANIFEST_NAME = "fancy-plugin"

BODY = """
from core.plugins import Plugin


class FancyPlugin(Plugin):
    async def initialize(self, config=None):
        self._initialized = True
"""


@pytest.fixture(autouse=True)
def _clean_sys_modules(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BASELITH_SKIP_INTEGRITY_CHECK", "true")
    before = set(sys.modules)
    yield
    for key in set(sys.modules) - before:
        if key.startswith("plugins."):
            del sys.modules[key]


@pytest.fixture
def controller(tmp_path: Path) -> HotReloadController:
    plugin_dir = tmp_path / DIR_NAME
    plugin_dir.mkdir()
    (plugin_dir / "manifest.yaml").write_text(
        f"name: {MANIFEST_NAME}\nversion: 1.0.0\n", encoding="utf-8"
    )
    (plugin_dir / "plugin.py").write_text(BODY, encoding="utf-8")

    registry = PluginRegistry()
    lifecycle = PluginLifecycleManager()
    loader = PluginLoader(tmp_path, registry, lifecycle)
    return HotReloadController(loader, registry, lifecycle)


class TestNameResolution:
    def test_directory_name_resolves_to_manifest_name(self, controller):
        assert controller.resolve_plugin_name(DIR_NAME) == MANIFEST_NAME
        assert controller.resolve_plugin_name(MANIFEST_NAME) == MANIFEST_NAME

    def test_unknown_name_is_returned_unchanged(self, controller):
        assert controller.resolve_plugin_name("nope") == "nope"


class TestLifecycleByDirectoryName:
    async def test_enable_is_keyed_by_manifest_name(self, controller):
        assert await controller.enable_plugin(DIR_NAME)

        assert controller.lifecycle.get_state(MANIFEST_NAME) == PluginState.ACTIVE
        assert controller.lifecycle.get_state(DIR_NAME) is None
        assert controller.registry.get(MANIFEST_NAME) is not None

    async def test_second_enable_does_not_double_load(self, controller):
        assert await controller.enable_plugin(DIR_NAME)
        first = controller.lifecycle.get_plugin_instance(MANIFEST_NAME)

        assert await controller.enable_plugin(DIR_NAME)

        assert controller.lifecycle.get_plugin_instance(MANIFEST_NAME) is first
        assert controller.lifecycle.get_state(MANIFEST_NAME) == PluginState.ACTIVE

    async def test_disable_by_directory_name(self, controller):
        assert await controller.enable_plugin(DIR_NAME)

        assert await controller.disable_plugin(DIR_NAME)

        assert controller.lifecycle.get_state(MANIFEST_NAME) == PluginState.DISABLED
        assert controller.registry.get(MANIFEST_NAME) is None

    async def test_reenable_after_disable(self, controller):
        assert await controller.enable_plugin(DIR_NAME)
        assert await controller.disable_plugin(DIR_NAME)

        assert await controller.enable_plugin(DIR_NAME)

        assert controller.lifecycle.get_state(MANIFEST_NAME) == PluginState.ACTIVE

    async def test_reload_by_directory_name(self, controller):
        assert await controller.enable_plugin(DIR_NAME)
        first = controller.lifecycle.get_plugin_instance(MANIFEST_NAME)

        assert await controller.reload_plugin(DIR_NAME)

        second = controller.lifecycle.get_plugin_instance(MANIFEST_NAME)
        assert second is not None and second is not first
        assert controller.lifecycle.get_state(MANIFEST_NAME) == PluginState.ACTIVE
