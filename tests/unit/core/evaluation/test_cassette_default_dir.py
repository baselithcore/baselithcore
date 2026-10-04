"""The default cassette library never points into the installed package."""

from __future__ import annotations

from pathlib import Path

import pytest

import core
from core.evaluation.cassette import CASSETTE_DIR, Cassette


def test_default_dir_is_cwd_relative() -> None:
    assert not CASSETTE_DIR.is_absolute()


def test_default_save_writes_under_the_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    path = Cassette(name="demo", turns=[]).save()
    assert path.resolve() == tmp_path / "tests" / "golden" / "cassettes" / "demo.json"
    install_root = Path(core.__file__).resolve().parents[1]
    assert not path.resolve().is_relative_to(install_root)
    assert Cassette.load("demo").name == "demo"
