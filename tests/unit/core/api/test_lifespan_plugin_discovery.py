"""The lazy-import discovery path must see entry-point plugins too.

``lifespan`` registers plugins through ``ResourceAnalyzer.discover_plugins``,
which only ever walked ``plugins/``. A plugin installed as a wheel and
advertised through the ``baselith.plugins`` entry-point group was therefore
invisible to every runtime surface that reads the registry's discovered set —
routes, UI tabs, flow handlers, lazy activation — even though
``PluginLoader.discover_plugins`` had found it.

These tests pin the merged view, and the precedence rule that goes with it: a
tree an operator dropped into ``plugins/`` wins over an installed package of
the same name.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.plugins.discovery import ENTRY_POINT_GROUP
from core.plugins.resource_analyzer import ResourceAnalyzer

PLUGIN_SOURCE = """
from core.plugins import Plugin


class DemoPlugin(Plugin):
    def get_router_prefix(self):
        return "/api/{prefix}"

    def get_routers(self):
        return []
"""


def _write_plugin(root: Path, name: str, *, version: str = "1.0.0") -> Path:
    plugin_dir = root / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "manifest.yaml").write_text(
        f"name: {name}\nversion: {version}\n", encoding="utf-8"
    )
    (plugin_dir / "plugin.py").write_text(
        PLUGIN_SOURCE.format(prefix=name), encoding="utf-8"
    )
    (plugin_dir / "__init__.py").write_text("", encoding="utf-8")
    return plugin_dir


class _FakeEntryPoint:
    def __init__(self, name: str, value: str) -> None:
        self.name = name
        self.value = value
        self.group = ENTRY_POINT_GROUP


class TestAnalyzerMergesExtraDirs:
    def test_entry_point_plugin_is_discovered(self, tmp_path):
        local_root = tmp_path / "plugins"
        local_root.mkdir()
        _write_plugin(local_root, "from_dir")

        site_packages = tmp_path / "site-packages"
        installed = _write_plugin(site_packages, "from_wheel")

        analyzer = ResourceAnalyzer(local_root)
        discoveries = analyzer.discover_plugins({}, extra_dirs=[installed])

        assert sorted(discoveries) == ["from_dir", "from_wheel"]

    def test_directory_wins_over_installed_package(self, tmp_path):
        local_root = tmp_path / "plugins"
        local_root.mkdir()
        _write_plugin(local_root, "shared", version="9.9.9")

        site_packages = tmp_path / "site-packages"
        installed = _write_plugin(site_packages, "shared", version="0.0.1")

        analyzer = ResourceAnalyzer(local_root)
        discoveries = analyzer.discover_plugins({}, extra_dirs=[installed])

        assert list(discoveries) == ["shared"]
        assert discoveries["shared"].metadata.version == "9.9.9"

    def test_config_filter_still_applies_to_extra_dirs(self, tmp_path):
        local_root = tmp_path / "plugins"
        local_root.mkdir()

        site_packages = tmp_path / "site-packages"
        installed = _write_plugin(site_packages, "from_wheel")

        analyzer = ResourceAnalyzer(local_root)
        assert analyzer.discover_plugins({"other": {}}, extra_dirs=[installed]) == {}

        enabled = analyzer.discover_plugins(
            {"from_wheel": {"enabled": True}}, extra_dirs=[installed]
        )
        assert list(enabled) == ["from_wheel"]

    def test_disabled_extra_dir_is_skipped(self, tmp_path):
        local_root = tmp_path / "plugins"
        local_root.mkdir()

        site_packages = tmp_path / "site-packages"
        installed = _write_plugin(site_packages, "from_wheel")

        analyzer = ResourceAnalyzer(local_root)
        discoveries = analyzer.discover_plugins(
            {"from_wheel": {"enabled": False}}, extra_dirs=[installed]
        )
        assert discoveries == {}

    def test_no_extra_dirs_is_unchanged(self, tmp_path):
        local_root = tmp_path / "plugins"
        local_root.mkdir()
        _write_plugin(local_root, "only_local")

        analyzer = ResourceAnalyzer(local_root)
        assert list(analyzer.discover_plugins({})) == ["only_local"]

    def test_missing_plugins_dir_still_yields_extra_dirs(self, tmp_path):
        site_packages = tmp_path / "site-packages"
        installed = _write_plugin(site_packages, "from_wheel")

        analyzer = ResourceAnalyzer(tmp_path / "does-not-exist")
        discoveries = analyzer.discover_plugins({}, extra_dirs=[installed])
        assert list(discoveries) == ["from_wheel"]


class TestResourceRequirements:
    """Discovery alone is not enough — lazy-init must bring the resources up."""

    def test_entry_point_resources_enter_the_init_set(self, tmp_path):
        local_root = tmp_path / "plugins"
        local_root.mkdir()
        _write_plugin(local_root, "from_dir")

        site_packages = tmp_path / "site-packages"
        installed = _write_plugin(site_packages, "from_wheel")
        (installed / "manifest.yaml").write_text(
            "name: from_wheel\n"
            "version: 1.0.0\n"
            "required_resources:\n"
            "- postgres\n"
            "optional_resources:\n"
            "- redis\n",
            encoding="utf-8",
        )

        analyzer = ResourceAnalyzer(local_root)

        # Without the extra dirs the plugin is invisible to lazy-init: it would
        # activate against a postgres nobody started.
        blind = analyzer.analyze_requirements({})
        assert "postgres" not in blind["required"]

        seeing = analyzer.analyze_requirements({}, extra_dirs=[installed])
        assert "postgres" in seeing["required"]
        assert "redis" in seeing["optional"]

    def test_disabled_entry_point_plugin_contributes_nothing(self, tmp_path):
        local_root = tmp_path / "plugins"
        local_root.mkdir()

        site_packages = tmp_path / "site-packages"
        installed = _write_plugin(site_packages, "from_wheel")
        (installed / "manifest.yaml").write_text(
            "name: from_wheel\nversion: 1.0.0\nrequired_resources:\n- postgres\n",
            encoding="utf-8",
        )

        analyzer = ResourceAnalyzer(local_root)
        requirements = analyzer.analyze_requirements(
            {"from_wheel": {"enabled": False}}, extra_dirs=[installed]
        )
        assert requirements["required"] == set()


class TestLoaderFeedsTheLifespanPath:
    """End-to-end of the wiring lifespan uses: loader → analyzer → registry."""

    def test_entry_point_reaches_the_registry(self, tmp_path, monkeypatch):
        from core.plugins import PluginLoader, PluginRegistry

        local_root = tmp_path / "plugins"
        local_root.mkdir()
        _write_plugin(local_root, "from_dir")

        site_packages = tmp_path / "site-packages"
        _write_plugin(site_packages, "from_wheel")
        monkeypatch.syspath_prepend(str(site_packages))
        monkeypatch.setattr(
            "core.plugins.discovery.entry_points",
            lambda group: [_FakeEntryPoint("from_wheel", "from_wheel")],
        )

        registry = PluginRegistry()
        loader = PluginLoader(local_root, registry)
        analyzer = ResourceAnalyzer(local_root)

        # Exactly the call core/api/lifespan.py now makes.
        discoveries = analyzer.discover_plugins(
            {}, extra_dirs=loader.discover_plugins()
        )
        for discovery in discoveries.values():
            registry.register_discovered_plugin(discovery)

        assert registry.get_discovered_plugin("from_wheel") is not None
        assert registry.match_plugin_route("/api/from_wheel/x") == "from_wheel"


@pytest.mark.parametrize("group", [ENTRY_POINT_GROUP])
def test_entry_point_group_name_is_stable(group: str) -> None:
    """Distributions declare this literal string; it is a public contract."""
    assert group == "baselith.plugins"
