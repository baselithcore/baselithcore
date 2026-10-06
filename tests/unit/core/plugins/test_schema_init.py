"""Schema creation belongs to the deploy, not to the serving process.

A deployment that isolates tenants at the database connects as a role that
owns nothing and holds no DDL — that is what makes a row-level-security policy
apply to it. A plugin that builds its schema at boot cannot live there, so the
work moved to `Plugin.init_schema`, run by `baselith plugin schema-init` with
the owner credential. These tests pin the seam and the command that drives it.
"""

from __future__ import annotations

from typing import Any

import pytest

from core.cli.commands.plugin.schema_init import _declares_schema, _run
from core.plugins.interface import Plugin

pytestmark = pytest.mark.unit


class _Quiet(Plugin):
    """A plugin with no schema of its own — the common case."""

    @property
    def metadata(self) -> Any:  # pragma: no cover - never read in these tests
        raise NotImplementedError


class _Schemaful(Plugin):
    def __init__(self) -> None:
        super().__init__()
        self.seen: list[dict[str, Any] | None] = []

    @property
    def metadata(self) -> Any:  # pragma: no cover - never read in these tests
        raise NotImplementedError

    async def init_schema(self, config: dict[str, Any] | None = None) -> None:
        self.seen.append(config)


class _Broken(_Schemaful):
    async def init_schema(self, config: dict[str, Any] | None = None) -> None:
        raise RuntimeError("permission denied for schema public")


def test_the_default_is_a_no_op() -> None:
    """A plugin whose tables come from a migration implements nothing."""
    assert _declares_schema(_Quiet()) is False


def test_an_override_is_detected() -> None:
    assert _declares_schema(_Schemaful()) is True


async def test_the_default_seam_does_nothing_and_says_so() -> None:
    assert await _Quiet().init_schema() is None


def _arrange(monkeypatch, candidates: list[tuple[str, Any, dict[str, Any]]]) -> None:
    """Point the command at a fixed plugin set and a no-op tenant scope."""
    import core.cli.commands.plugin.schema_init as module
    import core.db.connection as conn_module

    async def _loader(_name: str | None) -> list[tuple[str, Any, dict[str, Any]]]:
        return candidates

    monkeypatch.setattr(module, "_load_enabled", _loader)

    class _Scope:
        def __enter__(self) -> None:
            return None

        def __exit__(self, *exc: object) -> bool:
            return False

    monkeypatch.setattr(conn_module, "system_tenant_scope", lambda: _Scope())


async def test_a_clean_run_exits_zero(monkeypatch, capsys) -> None:
    plugin = _Schemaful()
    _arrange(monkeypatch, [("notes", plugin, {"persistence": "postgres"})])

    assert await _run(None, json_output=False) == 0
    # The plugin's own config block reaches it: a cold-loaded plugin has none
    # of its own, so passing it is the only way the store knows its backend.
    assert plugin.seen == [{"persistence": "postgres"}]


async def test_a_failure_becomes_the_exit_code(monkeypatch, capsys) -> None:
    """A deploy Job must stop rather than start an application against a
    half-built schema."""
    _arrange(monkeypatch, [("auth", _Broken(), {}), ("notes", _Schemaful(), {})])

    assert await _run(None, json_output=False) == 1
    captured = capsys.readouterr()
    # Errors go to stderr (core.cli.ui.print_error), so a Job's log keeps the
    # failure separable from the progress.
    assert "permission denied for schema public" in captured.err
    # The one that worked is still reported: a partial run is not a silent one.
    assert "notes" in captured.out


async def test_one_failure_does_not_stop_the_others(monkeypatch) -> None:
    survivor = _Schemaful()
    _arrange(monkeypatch, [("auth", _Broken(), {}), ("notes", survivor, {})])

    await _run(None, json_output=False)
    assert survivor.seen == [{}]


async def test_a_plugin_without_schema_is_neither_run_nor_failed(
    monkeypatch, capsys
) -> None:
    _arrange(monkeypatch, [("quiet", _Quiet(), {})])

    assert await _run(None, json_output=True) == 0
    import json

    report = json.loads(capsys.readouterr().out)
    assert report == {"initialised": [], "no_schema": ["quiet"], "failed": []}


async def test_nothing_enabled_is_not_a_failure(monkeypatch) -> None:
    _arrange(monkeypatch, [])
    assert await _run(None, json_output=False) == 0


async def test_overlay_entries_register_before_discovery(monkeypatch) -> None:
    """schema-init must see a verified overlay release, not the bundled copy.

    Discovery only lists overlay entries that ``register_overlay_packages``
    recorded, and nothing on this path imports the ``plugins`` package that
    normally does it — so without an explicit call the old bundled plugin's
    schema would run.
    """
    import core.plugins.loader as loader_mod
    import core.plugins.overlay as overlay_mod
    from core.cli.commands.plugin import schema_init as module

    calls: list[str] = []
    monkeypatch.setattr(
        overlay_mod, "register_overlay_packages", lambda: calls.append("register") or []
    )

    def _discover(self: Any) -> list[Any]:
        calls.append("discover")
        return []

    monkeypatch.setattr(loader_mod.PluginLoader, "discover_plugins", _discover)
    monkeypatch.setattr(
        "core.plugins.config_file.read_plugin_configs", lambda *a, **k: {}
    )
    assert await module._load_enabled(None) == []
    assert calls == ["register", "discover"]


async def test_overlay_registration_failure_does_not_stop_schema_init(
    monkeypatch,
) -> None:
    import core.plugins.loader as loader_mod
    import core.plugins.overlay as overlay_mod
    from core.cli.commands.plugin import schema_init as module

    def _boom() -> list[str]:
        raise RuntimeError("bad overlay")

    monkeypatch.setattr(overlay_mod, "register_overlay_packages", _boom)
    monkeypatch.setattr(loader_mod.PluginLoader, "discover_plugins", lambda self: [])
    monkeypatch.setattr(
        "core.plugins.config_file.read_plugin_configs", lambda *a, **k: {}
    )
    assert await module._load_enabled(None) == []
