"""``baselith update preflight``: one line per item, exit 1 on a block, read-only."""

from __future__ import annotations

import json

from baselith.core.cli import __main__ as cli_main
from baselith.core.cli import handlers
from baselith.core.cli.commands.update import run_update

from .test_update_cli import _root


def _items(*levels: str):
    from baselith.core.plugin_updates.preflight import PreflightItem

    return [
        PreflightItem(code=f"c{i}", level=level, message=f"m{i}")
        for i, level in enumerate(levels)
    ]


def test_preflight_exit_1_on_block(monkeypatch, capsys):
    from baselith.core.cli.commands import _update_preflight

    seen = []

    def fake(subject, version):
        seen.append((subject, version))
        return _items("warn", "block")

    monkeypatch.setattr(_update_preflight, "_preflight_inputs", fake)
    args = _root().parse_args(["update", "preflight", "aura", "--version", "1.2.0"])
    assert handlers.cmd_update(args) == 1
    assert seen == [("aura", "1.2.0")]
    out = capsys.readouterr().out.splitlines()
    assert out == ["[warn] c0: m0", "[block] c1: m1"]


def test_preflight_exit_0_without_block(monkeypatch, capsys):
    from baselith.core.cli.commands import _update_preflight

    monkeypatch.setattr(
        _update_preflight, "_preflight_inputs", lambda s, v: _items("warn", "ok")
    )
    assert run_update("preflight", {"subject": "core"}) == 0


def test_preflight_json_is_a_list_of_items(monkeypatch, capsys):
    from baselith.core.cli.commands import _update_preflight

    monkeypatch.setattr(
        _update_preflight, "_preflight_inputs", lambda s, v: _items("ok")
    )
    args = _root().parse_args(["--format", "json", "update", "preflight", "core"])
    assert handlers.cmd_update(args) == 0
    body = json.loads(capsys.readouterr().out)
    assert body == [{"code": "c0", "level": "ok", "message": "m0"}]


def test_preflight_failure_exits_1(monkeypatch, capsys):
    from baselith.core.cli.commands import _update_preflight

    def boom(subject, version):
        raise RuntimeError("x")

    monkeypatch.setattr(_update_preflight, "_preflight_inputs", boom)
    assert run_update("preflight", {"subject": "core"}) == 1
    assert "RuntimeError" in capsys.readouterr().out


def test_preflight_without_subject_is_usage_error():
    assert run_update("preflight", {}) == 2


def test_preflight_never_loads_plugin_code():
    assert cli_main._skips_plugin_clis(["update", "preflight", "core"])


def test_preflight_over_a_real_empty_state(monkeypatch, tmp_path, capsys):
    """End to end on this host: no report yet is a block, never a crash."""
    from baselith.core.config.plugin_update_apply import UpdateApplyConfig
    from baselith.core.config.plugin_updates import get_plugin_update_config
    from baselith.core.plugin_updates import service as svc_mod

    monkeypatch.setenv("PLUGIN_UPDATE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(
        svc_mod,
        "get_update_apply_config",
        lambda: UpdateApplyConfig(state_dir=tmp_path / "apply"),
    )
    get_plugin_update_config.cache_clear()
    try:
        code = run_update("preflight", {"subject": "core"})
    finally:
        get_plugin_update_config.cache_clear()
    assert code == 1
    assert capsys.readouterr().out.startswith("[block] candidate_unknown:")
    assert list(tmp_path.iterdir()) == []  # nothing was written
