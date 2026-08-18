from __future__ import annotations

import sys

import pytest

from scripts import mypy_runner
from scripts.mypy_runner import MypyNotFoundError, mypy_base_command


def test_prefers_current_interpreter_when_mypy_importable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mypy_runner.importlib.util, "find_spec", lambda name: object())

    assert mypy_base_command() == [sys.executable, "-m", "mypy"]


def test_falls_back_to_path_executable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mypy_runner.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(mypy_runner.shutil, "which", lambda name: "/usr/local/bin/mypy")

    assert mypy_base_command() == ["/usr/local/bin/mypy"]


def test_raises_when_mypy_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mypy_runner.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(mypy_runner.shutil, "which", lambda name: None)

    with pytest.raises(MypyNotFoundError, match="pip install"):
        mypy_base_command()
