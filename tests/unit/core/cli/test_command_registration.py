"""Command registration is loud on failure, and the package imports lazily.

A core command whose module failed to import used to disappear from ``--help``
without a word unless ``BASELITH_CLI_DEBUG`` was set, which reads as "no such
feature". And ``core/cli/__init__.py`` imported ``__main__`` eagerly, so
``python -m core.cli`` tripped runpy's "found in sys.modules" RuntimeWarning.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import core.cli.__main__ as cli_main

REPO_ROOT = Path(__file__).resolve().parents[4]


def _run_version(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)  # no plugins/ dir: skip the plugin CLI scan
    monkeypatch.setattr(cli_main, "ensure_checkout_precedence", lambda: None)
    monkeypatch.setattr(cli_main, "_configure_cli_logging", lambda: None)
    monkeypatch.setattr(sys, "argv", ["baselith", "--version"])
    with pytest.raises(SystemExit):
        cli_main.main()


class TestBrokenCommandIsReported:
    def test_warning_without_debug_flag(self, monkeypatch, tmp_path, capsys):
        monkeypatch.delenv("BASELITH_CLI_DEBUG", raising=False)
        monkeypatch.setattr(cli_main, "COMMANDS", ["__missing_command__"])

        _run_version(monkeypatch, tmp_path)

        err = capsys.readouterr().err
        assert "command '__missing_command__' unavailable" in err
        assert "ModuleNotFoundError" in err
        assert err.count("\n") == 1  # one line, no traceback

    def test_healthy_commands_stay_quiet(self, monkeypatch, tmp_path, capsys):
        monkeypatch.delenv("BASELITH_CLI_DEBUG", raising=False)
        monkeypatch.setattr(cli_main, "COMMANDS", ["info"])

        _run_version(monkeypatch, tmp_path)

        assert "unavailable" not in capsys.readouterr().err


class TestLazyPackage:
    def test_importing_the_package_does_not_run_main_module(self):
        code = "import sys, core.cli; print('core.cli.__main__' in sys.modules)"
        out = subprocess.run(
            [sys.executable, "-c", code],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        assert out.stdout.strip() == "False"

    def test_main_still_resolves(self):
        from core.cli import main

        assert main is cli_main.main
