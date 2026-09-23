"""A plugin count counts plugins, not directories.

A removed or renamed plugin leaves gitignored residue under ``plugins/``
(``__pycache__``, a built ``ui/``, local data). ``baselith info`` counted every
directory and reported 41 plugins on a checkout carrying 30.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.cli.commands.doctor_plugin_checks import check_plugins, local_plugins


@pytest.fixture
def checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    plugins = tmp_path / "plugins"
    (plugins / "with_manifest").mkdir(parents=True)
    (plugins / "with_manifest" / "manifest.yaml").write_text("name: with_manifest\n")
    (plugins / "with_entrypoint").mkdir()
    (plugins / "with_entrypoint" / "plugin.py").write_text("")
    (plugins / "with_entrypoint" / "manifest.yaml").write_text(
        "name: with_entrypoint\n"
    )
    (plugins / "with_manifest" / "plugin.py").write_text("")
    # Residue of removed plugins: a directory, not a plugin.
    (plugins / "__pycache__").mkdir()
    (plugins / "old_plugin" / "ui" / "node_modules").mkdir(parents=True)
    (plugins / "old_plugin" / "__pycache__").mkdir()
    (plugins / ".hidden").mkdir()
    (tmp_path / "pyproject.toml").write_text('name = "demo"\n')
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_local_plugins_skips_residue(checkout: Path) -> None:
    assert [p.name for p in local_plugins()] == ["with_entrypoint", "with_manifest"]


def test_doctor_and_info_agree(
    checkout: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from core.cli.commands.info import run_info

    assert check_plugins().message == "2 plugin(s) found"
    run_info(json_output=True)
    assert json.loads(capsys.readouterr().out)["project"]["plugin_count"] == 2
