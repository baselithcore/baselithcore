"""Plugins shipped inside the installed distribution are opt-in.

A plain ``pip install baselith-core`` has no ``configs/plugins.yaml``, and an
empty configuration used to enable every plugin the loader could see — which,
since discovery also reads the wheel's own ``plugins`` package, meant every
bundled plugin (routes, browser and computer-use agents included) activated on
a fresh install. A bundled plugin now runs only when a plugin config names it;
the user's own plugin root keeps the "empty config enables all" rule.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.plugins import discovery
from core.plugins.config_file import plugin_enabled

pytestmark = [pytest.mark.unit]


def _plugin(root: Path, name: str, deps: list[str] | None = None) -> Path:
    path = root / name
    path.mkdir(parents=True)
    (path / "plugin.py").write_text("X = 1\n")
    manifest = f"name: {name}\nversion: 1.0.0\n"
    if deps:
        manifest += "python_dependencies:\n" + "".join(f"  - {d}\n" for d in deps)
    (path / "manifest.yaml").write_text(manifest)
    return path


@pytest.fixture
def bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A ``plugins`` package inside a fake ``site-packages``."""
    site = tmp_path / "site-packages"
    bundled = site / "plugins"
    _plugin(bundled, "shipped", deps=["definitely-not-installed-pkg-xyz>=1"])
    _plugin(bundled, "other")
    monkeypatch.setattr(discovery, "installed_plugins_dir", lambda: bundled)
    monkeypatch.setattr(discovery, "_site_package_roots", lambda: [site])
    monkeypatch.setenv("BASELITH_DISABLE_PLUGIN_ENTRY_POINTS", "1")
    monkeypatch.delenv("PLUGIN_CONFIG_PATH", raising=False)
    return bundled


# --- the rule -----------------------------------------------------------------


class TestRule:
    def test_user_root_keeps_empty_config_enables_all(self) -> None:
        assert plugin_enabled({}, "mine", "mine") is True

    def test_bundled_needs_a_naming_config(self) -> None:
        assert plugin_enabled({}, "shipped", "shipped", bundled=True) is False
        assert plugin_enabled({"x": {}}, "shipped", "shipped", bundled=True) is False

    def test_bundled_named_without_enabled_key_runs(self) -> None:
        assert plugin_enabled({"shipped": {}}, "shipped", "shipped", bundled=True)

    def test_bundled_named_but_disabled_stays_off(self) -> None:
        configs = {"shipped": {"enabled": False}}
        assert not plugin_enabled(configs, "shipped", "shipped", bundled=True)


class TestIsBundledInstallDir:
    def test_child_of_installed_package(self, bundle: Path) -> None:
        assert discovery.is_bundled_install_dir(bundle / "shipped")

    def test_user_root_is_not_bundled(self, bundle: Path, tmp_path: Path) -> None:
        mine = _plugin(tmp_path / "project" / "plugins", "mine")
        assert not discovery.is_bundled_install_dir(mine)

    def test_entry_point_package_elsewhere_in_site_packages(self, bundle: Path) -> None:
        ep = _plugin(bundle.parent / "acme_plugin_pkg", "acme")
        assert not discovery.is_bundled_install_dir(ep)

    def test_source_checkout_is_not_bundled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        checkout = tmp_path / "repo" / "plugins"
        shipped = _plugin(checkout, "shipped")
        monkeypatch.setattr(discovery, "installed_plugins_dir", lambda: checkout)
        monkeypatch.setattr(discovery, "_site_package_roots", lambda: [tmp_path / "sp"])
        assert not discovery.is_bundled_install_dir(shipped)


# --- every activation path applies it ------------------------------------------


class TestDiscovery:
    def test_fresh_install_discovers_nothing(self, bundle: Path) -> None:
        from core.plugins.resource_analyzer import ResourceAnalyzer

        # No ./plugins: the configured root *is* the installed package.
        assert ResourceAnalyzer(bundle).discover_plugins({}) == {}

    def test_user_plugins_still_run_without_config(
        self, bundle: Path, tmp_path: Path
    ) -> None:
        from core.plugins.resource_analyzer import ResourceAnalyzer

        user = tmp_path / "project" / "plugins"
        _plugin(user, "mine")
        assert set(ResourceAnalyzer(user).discover_plugins({})) == {"mine"}

    def test_named_bundled_plugin_is_discovered(self, bundle: Path) -> None:
        from core.plugins.resource_analyzer import ResourceAnalyzer

        found = ResourceAnalyzer(bundle).discover_plugins({"shipped": {}})
        assert set(found) == {"shipped"}

    def test_requirements_ignore_disabled_bundled(self, bundle: Path) -> None:
        from core.plugins.resource_analyzer import ResourceAnalyzer

        (bundle / "shipped" / "manifest.yaml").write_text(
            "name: shipped\nversion: 1.0.0\nrequired_resources: [postgres]\n"
        )
        reqs = ResourceAnalyzer(bundle).analyze_requirements({})
        assert "postgres" not in reqs["required"]


async def test_bulk_load_skips_disabled_bundled(bundle: Path, tmp_path: Path) -> None:
    from core.plugins.loader import PluginLoader
    from core.plugins.registry import PluginRegistry

    user = tmp_path / "project" / "plugins"
    _plugin(user, "mine")
    loader = PluginLoader(user, PluginRegistry())
    loader.load_plugin = AsyncMock(return_value=None)  # type: ignore[method-assign]

    await loader.load_all_plugins({})

    loaded = {call.args[0].name for call in loader.load_plugin.await_args_list}
    assert loaded == {"mine"}


async def test_auto_activate_skips_disabled_bundled(bundle: Path) -> None:
    from core.api._plugin_runtime import PluginRuntimeHooks
    from core.plugins.resource_analyzer import ResourceAnalyzer

    analyzer = ResourceAnalyzer(bundle)
    discoveries = {
        name: analyzer.discover_plugin(bundle / name) for name in ("shipped", "other")
    }
    hooks = PluginRuntimeHooks(MagicMock(), MagicMock(), {}, MagicMock(), MagicMock())
    hooks.activate_plugin_for_runtime = AsyncMock(return_value=True)  # type: ignore[method-assign]

    await hooks.auto_activate(discoveries)

    hooks.activate_plugin_for_runtime.assert_not_awaited()


def test_app_middleware_skips_disabled_bundled(
    bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core.plugins import app_setup

    (bundle / "shipped" / "plugin.py").write_text(
        "class P:\n    def setup_app_middleware(self, app):\n        pass\n"
    )
    monkeypatch.setattr(app_setup, "_declares_setup_app_middleware", lambda _f: True)
    integrity = MagicMock(return_value=False)
    monkeypatch.setattr(app_setup, "verify_plugin_integrity", integrity)

    app_setup.apply_plugin_app_middleware(MagicMock(), bundle, {})
    integrity.assert_not_called()  # the enable gate stopped it first

    app_setup.apply_plugin_app_middleware(MagicMock(), bundle, {"shipped": {}})
    assert [c.args[0].name for c in integrity.call_args_list] == ["shipped"]


async def test_schema_init_skips_disabled_bundled(
    bundle: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import importlib

    schema_init = importlib.import_module("core.cli.commands.plugin.schema_init")
    monkeypatch.chdir(tmp_path)
    seen: list[str] = []

    async def _cold(_loader: object, plugin_dir: Path) -> object:
        seen.append(plugin_dir.name)
        plugin = MagicMock()
        plugin.metadata.name = plugin_dir.name
        return plugin

    monkeypatch.setattr(schema_init, "_load_cold", _cold)
    assert await schema_init._load_enabled(None) == []
    assert {"shipped", "other"} <= set(seen)


# --- operator surface ----------------------------------------------------------


def test_disabled_bundled_plugins_are_announced_once(
    bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dirs = [bundle / "shipped", bundle / "other"]
    log = MagicMock()
    monkeypatch.setattr(discovery, "logger", log)
    monkeypatch.setattr(discovery, "_ANNOUNCED_BUNDLED", set())

    assert discovery.announce_disabled_bundled({}, dirs) == ["other", "shipped"]
    discovery.announce_disabled_bundled({}, dirs)

    assert log.info.call_count == 1
    message = log.info.call_args.args[0] % log.info.call_args.args[1:]
    assert "baselith plugin enable" in message
    assert "other, shipped" in message
    assert discovery.announce_disabled_bundled({"shipped": {}}, dirs) == ["other"]


class TestDoctor:
    def _env(self, bundle: Path, tmp_path: Path, mp: pytest.MonkeyPatch) -> None:
        mp.chdir(tmp_path)
        mp.setenv("PLUGIN_PLUGINS_PATH", str(bundle))

    def test_dependency_check_ignores_disabled_bundled(
        self, bundle: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from core.cli.commands.doctor_plugin_checks import check_plugin_dependencies

        self._env(bundle, tmp_path, monkeypatch)
        assert check_plugin_dependencies().passed

    def test_dependency_check_covers_enabled_bundled(
        self, bundle: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from core.cli.commands.doctor_plugin_checks import check_plugin_dependencies

        self._env(bundle, tmp_path, monkeypatch)
        (tmp_path / "configs").mkdir()
        (tmp_path / "configs" / "plugins.yaml").write_text(
            "shipped:\n  enabled: true\n"
        )
        result = check_plugin_dependencies()
        assert not result.passed
        assert "shipped" in result.details


class TestCliEnable:
    def test_enable_bundled_writes_config_not_code(
        self, bundle: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import yaml

        from core.cli.commands.plugin.local_manage import enable_local_plugin

        monkeypatch.chdir(tmp_path)
        assert enable_local_plugin("shipped") == 0
        assert not (tmp_path / "plugins").exists()
        config = yaml.safe_load((tmp_path / "configs" / "plugins.yaml").read_text())
        assert config == {"shipped": {"enabled": True}}
        assert (bundle / "shipped" / "plugin.py").exists()

    def test_disable_bundled_writes_config_not_code(
        self, bundle: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import yaml

        from core.cli.commands.plugin.local_manage import disable_local_plugin

        monkeypatch.chdir(tmp_path)
        assert disable_local_plugin("shipped") == 0
        assert (bundle / "shipped" / "plugin.py").exists()
        config = yaml.safe_load((tmp_path / "configs" / "plugins.yaml").read_text())
        assert config == {"shipped": {"enabled": False}}

    def test_unknown_plugin_still_fails(
        self, bundle: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from core.cli.commands.plugin.local_manage import enable_local_plugin

        monkeypatch.chdir(tmp_path)
        assert enable_local_plugin("nope") == 1
        assert not (tmp_path / "configs").exists()
