"""One resolver for the plugin root; hook identity; enable reports restarts."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from core.config.plugins import (
    PluginConfig,
    installed_plugins_dir,
    resolve_plugins_root,
)
from core.plugins import Plugin, apply_late_app_hook
from core.plugins import app_setup as app_setup_mod
from core.plugins.app_setup import apply_plugin_app_middleware, plugin_restart_required


@pytest.fixture(autouse=True)
def _clean_modules() -> Any:
    before = {n for n in sys.modules if n == "plugins" or n.startswith("plugins.")}
    yield
    for name in list(sys.modules):
        if (name == "plugins" or name.startswith("plugins.")) and name not in before:
            del sys.modules[name]


# --- defect 1: the plugin root does not depend on the cwd alone -------------


def test_default_root_outside_checkout_is_installed_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)  # no plugins/ here
    monkeypatch.delenv("PLUGIN_PLUGINS_PATH", raising=False)
    assert PluginConfig().plugins_path == installed_plugins_dir()
    assert (installed_plugins_dir() / "__init__.py").is_file()


def test_cwd_plugins_dir_wins_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "plugins").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PLUGIN_PLUGINS_PATH", raising=False)
    assert PluginConfig().plugins_path == tmp_path / "plugins"


def test_explicit_absolute_path_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "elsewhere"
    monkeypatch.setenv("PLUGIN_PLUGINS_PATH", str(target))
    assert PluginConfig().plugins_path == target


def test_resolver_returns_cwd_path_when_nothing_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "core.config.plugins.installed_plugins_dir", lambda: tmp_path / "missing"
    )
    assert resolve_plugins_root("plugins") == tmp_path / "plugins"


def test_update_service_defaults_to_resolved_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import core.config.plugins as cfg_mod
    from core.config.plugin_updates import PluginUpdateConfig
    from core.plugin_updates.service import PluginUpdateService

    monkeypatch.setattr(
        cfg_mod, "_plugin_config", PluginConfig(plugins_path=tmp_path / "root")
    )
    svc = PluginUpdateService(
        PluginUpdateConfig(cache_dir=tmp_path / "c", core_update_repo="")
    )
    assert svc._bundled_root == tmp_path / "root"


# --- defect 2: hook identity and entry-point plugins ------------------------


def _write_plugin(plugin_dir: Path, marker: str) -> None:
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "manifest.json").write_text(
        '{"name": "%s", "version": "1.0.0"}' % plugin_dir.name
    )
    (plugin_dir / "plugin.py").write_text(
        f"""
from core.plugins import Plugin


class {marker}:
    pass


class MyPlugin(Plugin):
    @classmethod
    def setup_app_middleware(cls, app):
        app.add_middleware({marker})
"""
    )


class _FakeApp:
    def __init__(self) -> None:
        self.middleware: list[Any] = []

    def add_middleware(self, cls: Any, **kwargs: Any) -> None:
        self.middleware.append(cls)


def test_same_class_name_in_two_plugins_both_apply(tmp_path: Path) -> None:
    _write_plugin(tmp_path / "alpha_mw", "AlphaMarker")
    _write_plugin(tmp_path / "beta_mw", "BetaMarker")
    app = _FakeApp()
    assert apply_plugin_app_middleware(app, tmp_path, plugin_configs={}) == 2
    applied = app_setup_mod._applied_hooks(app)
    assert applied == {
        "plugins.alpha_mw.plugin.MyPlugin",
        "plugins.beta_mw.plugin.MyPlugin",
    }


def test_entry_point_plugins_get_their_middleware(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_plugin(tmp_path / "ep" / "ep_mw", "EpMarker")
    monkeypatch.setattr(
        app_setup_mod,
        "iter_entry_point_plugin_dirs",
        lambda: [tmp_path / "ep" / "ep_mw"],
    )
    app = _FakeApp()
    empty_root = tmp_path / "bundled"
    empty_root.mkdir()
    assert apply_plugin_app_middleware(app, empty_root, plugin_configs={}) == 1
    assert [cls.__name__ for cls in app.middleware] == ["EpMarker"]


# --- defect 3: a runtime enable reports a pending restart -------------------


class _LateMiddlewarePlugin(Plugin):
    metadata = SimpleNamespace(name="late-mw-restart")

    def __init__(self) -> None:
        pass

    @classmethod
    def setup_app_middleware(cls, app: Any) -> None:
        app.add_middleware(lambda a: a)


def test_restart_required_recorded_when_middleware_is_frozen() -> None:
    app = FastAPI()
    with TestClient(app):
        assert apply_late_app_hook(app, _LateMiddlewarePlugin()) is False
    assert plugin_restart_required(app, "late-mw-restart") is True
    assert plugin_restart_required(FastAPI(), "late-mw-restart") is False


def test_enable_endpoint_returns_restart_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.middleware.security import require_admin
    from core.plugins import api as api_mod

    class _Controller:
        lifecycle = SimpleNamespace(get_state=lambda name: None)

        def resolve_plugin_name(self, name: str) -> str:
            return name

        async def enable_plugin(self, name: str, config: Any) -> bool:
            return True

    app = FastAPI()
    app.include_router(api_mod.router)
    app.dependency_overrides[require_admin] = lambda: None
    monkeypatch.setattr(api_mod, "_controller", _Controller())
    app_setup_mod._RESTART_REQUIRED.setdefault(app, set()).add("needs-restart")
    with TestClient(app) as client:
        pending = client.post("/api/plugins/needs-restart/enable", json={}).json()
        live = client.post("/api/plugins/other/enable", json={}).json()
    assert pending["restart_required"] is True
    assert "restart" in pending["message"]
    assert live["restart_required"] is False
