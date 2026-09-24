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
    """Drop only the synthetic plugin modules this test created.

    Snapshotting up-front means we evict exclusively the ``plugins.*`` entries
    added during the test (the tmp_path fixtures), leaving the real plugin
    packages imported by other tests untouched — wiping those would break
    manifest lookups, Prometheus registration, and re-imports suite-wide.
    """
    before = {
        name for name in sys.modules if name == "plugins" or name.startswith("plugins.")
    }
    yield
    for name in list(sys.modules):
        if (name == "plugins" or name.startswith("plugins.")) and name not in before:
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
    # An empty enable-list: every plugin under tmp_path runs. Without it the
    # call would read the repository's configs/plugins.yaml, which names none
    # of these synthetic plugins.
    applied = apply_plugin_app_middleware(app, plugins_dir=tmp_path, plugin_configs={})

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


_HOOK_PLUGIN = """
from core.plugins import Plugin


class _Marker:
    pass


class HookPlugin(Plugin):
    @classmethod
    def setup_app_middleware(cls, app):
        app.add_middleware(_Marker, name=%r)
"""


def test_disabled_plugin_gets_no_hook(tmp_path):
    """Regression: the enable-list in plugins.yaml governed routers only.

    A plugin disabled there still had its class-level hook run during
    create_app(), so a release with a declarative plugin set served the SPA
    shell (or installed the ASGI middleware) of every plugin in the image.
    The hook must follow the same rule the lifespan applies.
    """
    _write_plugin(tmp_path / "on_plugin", _HOOK_PLUGIN % "on")
    _write_plugin(tmp_path / "off_plugin", _HOOK_PLUGIN % "off")

    app = _FakeApp()
    applied = apply_plugin_app_middleware(
        app,
        plugins_dir=tmp_path,
        plugin_configs={
            "on_plugin": {"enabled": True},
            "off_plugin": {"enabled": False},
        },
    )

    assert applied == 1
    assert [kwargs["name"] for _cls, kwargs in app.middleware] == ["on"]


def test_plugin_absent_from_non_empty_config_gets_no_hook(tmp_path):
    _write_plugin(tmp_path / "named_plugin", _HOOK_PLUGIN % "named")
    _write_plugin(tmp_path / "other_plugin", _HOOK_PLUGIN % "other")

    app = _FakeApp()
    applied = apply_plugin_app_middleware(
        app, plugins_dir=tmp_path, plugin_configs={"named_plugin": {"enabled": True}}
    )

    assert applied == 1
    assert [kwargs["name"] for _cls, kwargs in app.middleware] == ["named"]


def test_empty_config_applies_every_hook(tmp_path):
    _write_plugin(tmp_path / "one_plugin", _HOOK_PLUGIN % "one")
    _write_plugin(tmp_path / "two_plugin", _HOOK_PLUGIN % "two")

    app = _FakeApp()
    applied = apply_plugin_app_middleware(app, plugins_dir=tmp_path, plugin_configs={})

    assert applied == 2


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
    applied = apply_plugin_app_middleware(app, plugins_dir=tmp_path, plugin_configs={})
    assert applied == 0


def test_signature_gate_blocks_unsigned_plugin(tmp_path, monkeypatch):
    """Regression: this path verified integrity but not the publisher
    signature, so a plugin declaring setup_app_middleware reached exec_module
    with BASELITH_REQUIRE_PLUGIN_SIGNATURES bypassed — the integrity hash alone
    is recomputable by anyone who can write the plugin tree."""
    _write_plugin(
        tmp_path / "unsigned_plugin",
        """
from core.plugins import Plugin


class _Marker:
    pass


class UnsignedPlugin(Plugin):
    @classmethod
    def setup_app_middleware(cls, app):
        app.add_middleware(_Marker)
""",
    )

    refused: list[str] = []

    def _deny(plugin_name, integrity_hash_hex, signature_hex):
        refused.append(plugin_name)
        return False

    monkeypatch.setattr("core.plugins.app_setup.enforce_plugin_signature", _deny)

    app = _FakeApp()
    applied = apply_plugin_app_middleware(app, plugins_dir=tmp_path, plugin_configs={})

    assert applied == 0
    assert app.middleware == []
    assert refused, "the signature gate must run on this path"


def test_signature_gate_allows_signed_plugin(tmp_path, monkeypatch):
    _write_plugin(
        tmp_path / "signed_plugin",
        """
from core.plugins import Plugin


class _Marker:
    pass


class SignedPlugin(Plugin):
    @classmethod
    def setup_app_middleware(cls, app):
        app.add_middleware(_Marker)
""",
    )

    monkeypatch.setattr(
        "core.plugins.app_setup.enforce_plugin_signature",
        lambda *args, **kwargs: True,
    )

    app = _FakeApp()
    applied = apply_plugin_app_middleware(app, plugins_dir=tmp_path, plugin_configs={})

    assert applied == 1
    assert len(app.middleware) == 1


def test_explicit_plugins_path_setting_is_scanned(tmp_path, monkeypatch):
    # Regression: PLUGIN_PLUGINS_PATH steered marketplace installs only; the
    # runtime loaders kept scanning plugins/, so installed plugins never ran.
    import core.config.plugins as plugin_config_module
    from core.config.plugins import PluginConfig

    _write_plugin(
        tmp_path / "custom_root" / "mw_plugin",
        """
from core.plugins import Plugin


class _Marker:
    pass


class MwPlugin(Plugin):
    @classmethod
    def setup_app_middleware(cls, app):
        app.add_middleware(_Marker)
""",
    )
    monkeypatch.setattr(
        plugin_config_module,
        "_plugin_config",
        PluginConfig(plugins_path=tmp_path / "custom_root"),
    )
    app = _FakeApp()
    assert apply_plugin_app_middleware(app, plugin_configs={}) == 1
