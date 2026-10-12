"""The `baselith plugin` commands read the plugin root the app reads.

Since 1.0 a checkout keeps its plugins under ``baselith/plugins``, so a bare
``plugins`` taken against the cwd names nothing there: status, tree, deps,
validate and enable/disable all answered "not found" for plugins the server
was serving. They now go through :func:`plugins_root`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import baselith.core.config.plugins as config_mod

pytestmark = pytest.mark.unit


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A cwd with no ``plugins`` and an installed root holding ``alpha``."""
    installed = tmp_path / "site" / "baselith" / "plugins"
    plugin = installed / "alpha"
    plugin.mkdir(parents=True)
    (plugin / "__init__.py").write_text("")
    (plugin / "plugin.py").write_text("X = 1\n")
    (plugin / "manifest.yaml").write_text(
        "name: alpha\nversion: 1.0.0\ndescription: A test plugin\n"
    )
    cwd = tmp_path / "project"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.delenv("PLUGIN_PLUGINS_PATH", raising=False)
    monkeypatch.setattr(config_mod, "installed_plugins_dir", lambda: installed)
    return installed


def test_plugins_root_falls_back_to_the_installed_package(root: Path) -> None:
    assert config_mod.plugins_root() == root


def test_plugins_root_prefers_a_plugins_dir_in_the_cwd(root: Path) -> None:
    (Path.cwd() / "plugins").mkdir()
    assert config_mod.plugins_root() == Path.cwd() / "plugins"


def test_plugins_root_honours_the_configured_path(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.setenv("PLUGIN_PLUGINS_PATH", str(other))
    assert config_mod.plugins_root() == other


def test_deps_sees_the_installed_plugins(root: Path) -> None:
    from baselith.core.cli.commands.plugin import deps

    assert deps._local_plugin_names() == ["alpha"]
    assert deps._check_plugin_dep("alpha") is True


def test_tree_loads_the_installed_manifests(root: Path) -> None:
    from baselith.core.cli.commands.plugin import tree

    assert set(tree._load_all_manifests()) == {"alpha"}


def test_status_and_info_find_the_plugin(root: Path) -> None:
    from baselith.core.cli.commands.plugin import local_status

    assert local_status.status_local_plugins("alpha", json_output=True) == 0
    assert local_status.info_local_plugin("alpha") == 0


def test_validate_finds_the_plugin(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from baselith.core.cli.commands.plugin import local_validate

    errors: list[str] = []
    monkeypatch.setattr(
        local_validate, "print_error", lambda msg, *a, **k: errors.append(msg)
    )
    local_validate.validate_local_plugin("alpha", json_output=True)
    assert not [e for e in errors if "not found" in e]


def test_plugin_subcommands_register_from_the_configured_root(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plugin's ``cli.py`` is found without a ``plugins`` dir in the cwd."""
    import importlib
    import types

    from baselith.core.cli import __main__ as cli_main

    (root / "alpha" / "cli.py").write_text("")
    seen: list[str] = []
    fake = types.SimpleNamespace(register_parser=lambda sub, fmt: seen.append("alpha"))
    real_import = importlib.import_module
    monkeypatch.setattr(
        cli_main.importlib,
        "import_module",
        lambda name: (
            fake if name == "baselith.plugins.alpha.cli" else real_import(name)
        ),
    )
    cli_main._register_plugin_clis(object(), object())
    assert seen == ["alpha"]
