"""Plugin installs never write into the installed package; discovery sees both.

``PLUGIN_PLUGINS_PATH`` resolves, by default, to the ``plugins`` package of the
installed distribution when the cwd has no ``plugins/`` — right for *reading*
the bundled plugins, wrong for *writing*: a marketplace install would land in
``site-packages``, be wiped by the next ``pip install -U`` and need write access
to the interpreter. Installs therefore resolve their own root
(:func:`core.config.plugins.plugin_install_root`), and discovery adds the
bundled package as a second, lower-precedence root so a project's
``./plugins`` does not hide the plugins the framework ships.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.config.plugins import PluginConfig, plugin_install_root
from core.plugins import discovery

pytestmark = [pytest.mark.unit]


@pytest.fixture
def no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PLUGIN_PLUGINS_PATH", raising=False)


# --- install target ----------------------------------------------------------


@pytest.mark.usefixtures("no_env")
def test_default_install_root_is_cwd_plugins_never_the_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)  # no plugins/ here: reads fall back to the package
    config = PluginConfig()

    assert plugin_install_root(config) == tmp_path / "plugins"
    assert plugin_install_root(config) != config.plugins_path


@pytest.mark.usefixtures("no_env")
def test_default_install_root_uses_an_existing_cwd_plugins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "plugins").mkdir()
    monkeypatch.chdir(tmp_path)

    assert plugin_install_root(PluginConfig()) == tmp_path / "plugins"


def test_explicit_relative_root_is_cwd_relative_even_when_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PLUGIN_PLUGINS_PATH", "my_plugins")

    assert plugin_install_root(PluginConfig()) == tmp_path / "my_plugins"


def test_explicit_absolute_root_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PLUGIN_PLUGINS_PATH", str(tmp_path / "data" / "plugins"))

    assert plugin_install_root(PluginConfig()) == tmp_path / "data" / "plugins"


def test_init_argument_counts_as_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PLUGIN_PLUGINS_PATH", raising=False)

    config = PluginConfig(plugins_path=Path("custom"))
    assert plugin_install_root(config) == tmp_path / "custom"


@pytest.mark.usefixtures("no_env")
async def test_marketplace_installer_targets_the_install_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import core.config.plugins as plugins_config
    from core.marketplace.installer import PluginInstaller

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(plugins_config, "_plugin_config", None)

    installer = PluginInstaller()
    assert installer.plugins_dir == tmp_path / "plugins"
    assert installer._resolve_plugin_dir("demo") == (tmp_path / "plugins" / "demo")


# --- discovery sees the bundled package beside the user root -----------------


def _plugin(root: Path, name: str) -> Path:
    path = root / name
    path.mkdir(parents=True)
    (path / "plugin.py").write_text("X = 1\n")
    (path / "manifest.yaml").write_text(f"name: {name}\n")
    return path


@pytest.fixture
def installed_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A ``plugins`` package inside a fake ``site-packages``."""
    site = tmp_path / "site-packages"
    bundled = site / "plugins"
    _plugin(bundled, "shipped")
    _plugin(bundled, "shared")
    (bundled / "__pycache__").mkdir()
    monkeypatch.setattr(discovery, "installed_plugins_dir", lambda: bundled)
    monkeypatch.setattr(discovery, "_site_package_roots", lambda: [site])
    return bundled


def test_bundled_root_is_added_when_it_is_installed(
    tmp_path: Path, installed_bundle: Path
) -> None:
    user = tmp_path / "project" / "plugins"
    _plugin(user, "mine")
    shared = _plugin(user, "shared")

    merged = discovery.with_bundled_plugins(user, [user / "mine", shared])

    assert [p.name for p in merged] == ["mine", "shared", "shipped"]
    # The user's tree wins a name clash.
    assert merged[1] == shared


def test_bundled_root_is_not_scanned_twice(installed_bundle: Path) -> None:
    assert discovery.bundled_plugins_root(installed_bundle) is None


def test_a_checkout_is_not_a_second_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = tmp_path / "repo" / "plugins"
    _plugin(checkout, "shipped")
    monkeypatch.setattr(discovery, "installed_plugins_dir", lambda: checkout)
    monkeypatch.setattr(discovery, "_site_package_roots", lambda: [tmp_path / "sp"])

    assert discovery.bundled_plugins_root(tmp_path / "elsewhere") is None


def test_loader_discovers_user_and_bundled_plugins(
    tmp_path: Path, installed_bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core.plugins.loader import PluginLoader
    from core.plugins.registry import PluginRegistry

    monkeypatch.setenv("BASELITH_DISABLE_PLUGIN_ENTRY_POINTS", "1")
    user = tmp_path / "project" / "plugins"
    _plugin(user, "mine")

    found = PluginLoader(user, PluginRegistry()).discover_plugins()

    assert sorted(p.name for p in found) == ["mine", "shared", "shipped"]


def test_resource_analyzer_sees_bundled_plugins(
    tmp_path: Path, installed_bundle: Path
) -> None:
    from core.plugins.resource_analyzer import ResourceAnalyzer

    user = tmp_path / "project" / "plugins"
    _plugin(user, "mine")

    # Bundled plugins are opt-in: an empty config leaves them off...
    assert set(ResourceAnalyzer(user).discover_plugins({})) == {"mine"}
    # ...and naming them brings them in beside the user's own.
    named = {"mine": {}, "shared": {}, "shipped": {}}
    found = ResourceAnalyzer(user).discover_plugins(named)

    assert {"mine", "shared", "shipped"} <= set(found)
