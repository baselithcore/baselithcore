"""Tests for the CLI checkout-precedence bootstrap.

The behaviour under test is the one that used to break RQ workers: running
``baselith`` from a checkout while ``import core`` resolves to an installed
distribution. See :mod:`core.cli.bootstrap`.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.cli import bootstrap


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    """A directory carrying the marker files of a Baselith checkout."""
    (tmp_path / "core" / "cli").mkdir(parents=True)
    (tmp_path / "core" / "__init__.py").write_text("")
    (tmp_path / "core" / "_version.py").write_text('__version__ = "9.9.9"\n')
    (tmp_path / "core" / "cli" / "__main__.py").write_text("")
    return tmp_path


def test_find_checkout_root_detects_marker_files(checkout: Path) -> None:
    assert bootstrap.find_checkout_root(checkout) == checkout.resolve()


def test_find_checkout_root_ignores_unrelated_dir(tmp_path: Path) -> None:
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "__init__.py").write_text("")
    assert bootstrap.find_checkout_root(tmp_path) is None


def test_core_is_from_true_for_own_tree() -> None:
    import core

    root = Path(core.__file__).resolve().parent.parent
    assert bootstrap.core_is_from(root) is True


def test_core_is_from_false_for_foreign_root(tmp_path: Path) -> None:
    assert bootstrap.core_is_from(tmp_path.resolve()) is False


def test_build_child_env_puts_root_first(monkeypatch: pytest.MonkeyPatch) -> None:
    env = {"PYTHONPATH": os.pathsep.join(["/other", "/opt/app"])}
    child = bootstrap.build_child_env(Path("/opt/app"), env)
    assert child["PYTHONPATH"].split(os.pathsep) == ["/opt/app", "/other"]
    assert child[bootstrap.REEXEC_MARKER] == "1"


def test_build_child_env_without_existing_pythonpath() -> None:
    child = bootstrap.build_child_env(Path("/opt/app"), {})
    assert child["PYTHONPATH"] == "/opt/app"


def test_should_reexec_requires_a_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(bootstrap.REEXEC_MARKER, raising=False)
    monkeypatch.delenv(bootstrap.OPT_OUT, raising=False)
    assert bootstrap.should_reexec(None) is False


def test_should_reexec_true_on_mismatch(
    monkeypatch: pytest.MonkeyPatch, checkout: Path
) -> None:
    monkeypatch.delenv(bootstrap.REEXEC_MARKER, raising=False)
    monkeypatch.delenv(bootstrap.OPT_OUT, raising=False)
    assert bootstrap.should_reexec(checkout) is True


def test_should_reexec_false_when_core_matches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(bootstrap.REEXEC_MARKER, raising=False)
    monkeypatch.delenv(bootstrap.OPT_OUT, raising=False)
    monkeypatch.setattr(bootstrap, "core_is_from", lambda _root: True)
    assert bootstrap.should_reexec(tmp_path) is False


@pytest.mark.parametrize("flag", [bootstrap.REEXEC_MARKER, bootstrap.OPT_OUT])
def test_should_reexec_honours_env_flags(
    monkeypatch: pytest.MonkeyPatch, checkout: Path, flag: str
) -> None:
    monkeypatch.delenv(bootstrap.REEXEC_MARKER, raising=False)
    monkeypatch.delenv(bootstrap.OPT_OUT, raising=False)
    monkeypatch.setenv(flag, "1")
    assert bootstrap.should_reexec(checkout) is False


def test_ensure_checkout_precedence_execs_with_checkout(
    monkeypatch: pytest.MonkeyPatch, checkout: Path
) -> None:
    monkeypatch.delenv(bootstrap.REEXEC_MARKER, raising=False)
    monkeypatch.delenv(bootstrap.OPT_OUT, raising=False)
    monkeypatch.chdir(checkout)
    calls: list[tuple[str, list[str], dict[str, str]]] = []

    def fake_execve(exe: str, argv: list[str], env: dict[str, str]) -> None:
        calls.append((exe, argv, env))

    monkeypatch.setattr(os, "execve", fake_execve)
    bootstrap.ensure_checkout_precedence(["queue", "worker"])

    assert len(calls) == 1
    _exe, argv, env = calls[0]
    assert argv[1:] == ["-m", "core.cli", "queue", "worker"]
    assert env["PYTHONPATH"].split(os.pathsep)[0] == str(checkout.resolve())


def test_ensure_checkout_precedence_noop_outside_checkout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(bootstrap.REEXEC_MARKER, raising=False)
    monkeypatch.delenv(bootstrap.OPT_OUT, raising=False)
    monkeypatch.chdir(tmp_path)

    def _no_exec(*_args: object, **_kwargs: object) -> None:
        pytest.fail("must not re-exec")

    monkeypatch.setattr(os, "execve", _no_exec)
    bootstrap.ensure_checkout_precedence([])


def test_ensure_checkout_precedence_survives_exec_failure(
    monkeypatch: pytest.MonkeyPatch, checkout: Path
) -> None:
    monkeypatch.delenv(bootstrap.REEXEC_MARKER, raising=False)
    monkeypatch.delenv(bootstrap.OPT_OUT, raising=False)
    monkeypatch.chdir(checkout)

    def boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("exec denied")

    monkeypatch.setattr(os, "execve", boom)
    bootstrap.ensure_checkout_precedence([])  # must not raise
