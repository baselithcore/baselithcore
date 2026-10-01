"""runtime_state_paths: declaration, validation and the pre-install check."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.plugins.manifest_model import ManifestValidationError, validate_manifest_data
from core.plugins.runtime_state import (
    ALWAYS_CARRIED,
    MAX_RUNTIME_STATE_PATHS,
    declared_runtime_state_paths,
    normalize_runtime_state_paths,
    undeclared_runtime_files,
)


def _tree(root: Path, files: list[str]) -> None:
    for rel in files:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")


def test_normalize_accepts_and_dedupes() -> None:
    assert normalize_runtime_state_paths(
        ["var", "data/", "state/db.sqlite", "var"]
    ) == [
        "var",
        "data",
        "state/db.sqlite",
    ]
    assert normalize_runtime_state_paths(None) == []


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "/",
        "/abs",
        "../up",
        "a/../b",
        "./x",
        "a//b",
        "a\\b",
        "cache/*",
        "plugin.py",
        "manifest.yaml",
        "static/app.js",
        "__pycache__",
    ],
)
def test_normalize_refuses(bad: str) -> None:
    with pytest.raises(ValueError, match="runtime_state_paths"):
        normalize_runtime_state_paths([bad])


def test_normalize_refuses_non_strings_and_too_many() -> None:
    with pytest.raises(ValueError, match="runtime_state_paths"):
        normalize_runtime_state_paths([1])
    too_many = [f"d{i}" for i in range(MAX_RUNTIME_STATE_PATHS + 1)]
    with pytest.raises(ValueError, match="runtime_state_paths"):
        normalize_runtime_state_paths(too_many)


def test_manifest_accepts_scalar_and_list() -> None:
    assert validate_manifest_data(
        {"name": "demo", "runtime_state_paths": "var"}
    ).runtime_state_paths == ["var"]
    model = validate_manifest_data(
        {"name": "demo", "runtime_state_paths": ["var/", "db"]}
    )
    assert model.runtime_state_paths == ["var", "db"]
    assert validate_manifest_data({"name": "demo"}).runtime_state_paths is None


def test_manifest_refuses_unsafe_path() -> None:
    with pytest.raises(ManifestValidationError, match="runtime_state_paths"):
        validate_manifest_data({"name": "demo", "runtime_state_paths": ["../escape"]})


def test_undeclared_lists_only_unknown_files(tmp_path: Path) -> None:
    _tree(
        tmp_path,
        [
            "__init__.py",
            "manifest.yaml",
            "docs/a.md",
            "data/qdrant/seg",
            "var/db.sqlite",
            "notes.txt",
            "__pycache__/m.cpython-312.pyc",
            "sub/__pycache__/x.cpython-312.pyc",
            "stray.pyc",
        ],
    )
    known = {"__init__.py", "manifest.yaml", "docs/a.md"}
    assert undeclared_runtime_files(tmp_path, known, ["var"]) == [
        "data/qdrant/seg",
        "notes.txt",
    ]


def test_declared_path_is_a_prefix_not_a_substring(tmp_path: Path) -> None:
    _tree(tmp_path, ["var/x", "various/y"])
    assert undeclared_runtime_files(tmp_path, set(), ["var"]) == ["various/y"]


def test_symlink_is_reported_not_followed(tmp_path: Path) -> None:
    _tree(tmp_path, ["real/a"])
    (tmp_path / "link").symlink_to(tmp_path / "real", target_is_directory=True)
    assert undeclared_runtime_files(tmp_path, {"real/a"}, []) == ["link"]


def test_declared_runtime_state_paths_reads_manifest(tmp_path: Path) -> None:
    assert declared_runtime_state_paths(tmp_path) == []
    (tmp_path / "manifest.yaml").write_text(
        "name: demo\nruntime_state_paths:\n  - var/\n"
    )
    assert declared_runtime_state_paths(tmp_path) == ["var"]
    (tmp_path / "manifest.yaml").write_text("name: demo\nruntime_state_paths: [../x]\n")
    with pytest.raises(ValueError, match="runtime_state_paths"):
        declared_runtime_state_paths(tmp_path)


@pytest.mark.parametrize(
    "bad",
    [
        "ui",
        "ui/dist",
        "ui/dist/x",
        "static",
        "static/sub",
        "skills",
        "frontend",
        "templates",
        "locales",
        "ui/out",
        "ui/build",
    ],
)
def test_normalize_refuses_code_asset_roots(bad: str) -> None:
    with pytest.raises(ValueError, match="runtime_state_paths"):
        normalize_runtime_state_paths([bad])


def test_env_and_node_modules_are_not_undeclared(tmp_path: Path) -> None:
    _tree(tmp_path, [".env", "ui/node_modules/x/y.js", "node_modules/z", "stray"])
    assert ALWAYS_CARRIED == (".env",)
    assert undeclared_runtime_files(tmp_path, set(), []) == ["stray"]


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory modes")
def test_unreadable_directory_fails_closed(tmp_path: Path) -> None:
    _tree(tmp_path, ["locked/state"])
    (tmp_path / "locked").chmod(0)
    try:
        with pytest.raises(OSError, match="locked"):
            undeclared_runtime_files(tmp_path, set(), [])
    finally:
        (tmp_path / "locked").chmod(0o755)
