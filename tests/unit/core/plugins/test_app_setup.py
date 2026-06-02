"""Tests for synchronous plugin app-middleware discovery (app_setup.py)."""

import sys

import pytest

from core.plugins.app_setup import apply_plugin_app_middleware


class _FakeApp:
    """Captures add_middleware calls like a Starlette app."""

    def __init__(self) -> None:
        self.middleware: list = []

    def add_middleware(self, cls, **kwargs) -> None:
        self.middleware.append((cls, kwargs))


def _write_plugin(plugin_dir, body: str) -> None:
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "manifest.json").write_text(
        '{"name": "%s", "version": "1.0.0"}' % plugin_dir.name
    )
    (plugin_dir / "plugin.py").write_text(body)


@pytest.fixture(autouse=True)
def _clean_modules():
    """Drop synthetic plugin modules between tests to avoid cache bleed."""
    yield
    for name in list(sys.modules):
        if name == "plugins" or name.startswith("plugins."):
            del sys.modules[name]


def test_hook_applied_for_declaring_plugin(tmp_path):
    _write_plugin(
        tmp_path / "mw_plugin",
        """
from core.plugins import Plugin


class _Marker:
    pass


class MwPlugin(Plugin):
    @classmethod
    def setup_app_middleware(cls, app):
        app.add_middleware(_Marker, flag=True)
""",
    )

    app = _FakeApp()
    applied = apply_plugin_app_middleware(app, plugins_dir=tmp_path)

    assert applied == 1
    assert len(app.middleware) == 1
    assert app.middleware[0][1] == {"flag": True}


def test_plugin_without_hook_is_skipped(tmp_path):
    _write_plugin(
        tmp_path / "plain_plugin",
        """
from core.plugins import Plugin


class PlainPlugin(Plugin):
    async def initialize(self, config=None):
        await super().initialize(config or {})
""",
    )

    app = _FakeApp()
    applied = apply_plugin_app_middleware(app, plugins_dir=tmp_path)

    assert applied == 0
    assert app.middleware == []


def test_missing_plugins_dir_returns_zero(tmp_path):
    app = _FakeApp()
    assert apply_plugin_app_middleware(app, plugins_dir=tmp_path / "absent") == 0


def test_failing_hook_does_not_raise(tmp_path):
    _write_plugin(
        tmp_path / "boom_plugin",
        """
from core.plugins import Plugin


class BoomPlugin(Plugin):
    @classmethod
    def setup_app_middleware(cls, app):
        raise RuntimeError("boom")
""",
    )

    app = _FakeApp()
    # Best-effort: the failing hook is logged, not propagated, and counts as
    # not-applied.
    applied = apply_plugin_app_middleware(app, plugins_dir=tmp_path)
    assert applied == 0
