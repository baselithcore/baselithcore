"""Tests for the public API surface drift gate (scripts/check_public_api.py)."""

from __future__ import annotations

from pathlib import Path

from scripts.check_public_api import (
    BASELINE_PATH,
    REPO_ROOT,
    check_public_api,
    collect_public_api,
    diff_public_api,
    dynamic_packages,
    load_baseline,
    read_literal_all,
    write_baseline,
)


def _package(root: Path, dotted: str, body: str) -> None:
    directory = root.joinpath(*dotted.split("."))
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "__init__.py").write_text(body, encoding="utf-8")


def _tree(root: Path) -> None:
    _package(root, "core", '__all__ = ["__version__"]\n')
    _package(root, "core.alpha", 'from .a import A, B\n\n__all__ = ("B", "A")\n')
    _package(root, "core.beta", "__all__ = [\n    'run',\n]\n")
    _package(
        root, "core.dyn", "__all__ = [n for n in dir() if not n.startswith('_')]\n"
    )
    _package(root, "core.noall", "x = 1\n")


def test_collects_sorted_literal_alls_only(tmp_path: Path) -> None:
    _tree(tmp_path)

    assert collect_public_api(tmp_path) == {
        "core": ["__version__"],
        "core.alpha": ["A", "B"],
        "core.beta": ["run"],
    }
    assert dynamic_packages(tmp_path) == ["core.dyn", "core.noall"]


def test_augmented_all_is_treated_as_dynamic(tmp_path: Path) -> None:
    init = tmp_path / "__init__.py"
    init.write_text('__all__ = ["a"]\n__all__ += ["b"]\n', encoding="utf-8")

    assert read_literal_all(init) is None


def test_removed_symbol_is_breaking(tmp_path: Path) -> None:
    _tree(tmp_path)
    baseline = {
        "core": ["__version__"],
        "core.alpha": ["A", "B", "C"],
        "core.beta": ["run"],
    }

    violations = check_public_api(tmp_path, baseline=baseline)

    assert len(violations) == 1
    assert violations[0].startswith("BREAKING: core.alpha no longer exports C")


def test_added_symbol_requires_baseline_refresh(tmp_path: Path) -> None:
    _tree(tmp_path)
    baseline = {"core": ["__version__"], "core.alpha": ["A"], "core.beta": ["run"]}

    violations = check_public_api(tmp_path, baseline=baseline)

    assert len(violations) == 1
    assert "core.alpha newly exports B" in violations[0]
    assert "--update-baseline" in violations[0]


def test_vanished_package_removes_every_symbol(tmp_path: Path) -> None:
    _tree(tmp_path)
    baseline = {**collect_public_api(tmp_path), "core.gone": ["x", "y"]}

    diff = diff_public_api(baseline, collect_public_api(tmp_path))

    assert diff.removed == {"core.gone": ["x", "y"]}
    assert diff.added == {}


def test_baseline_roundtrip(tmp_path: Path) -> None:
    _tree(tmp_path)
    baseline_path = tmp_path / "baseline.json"

    assert write_baseline(tmp_path, baseline_path) == 4
    assert load_baseline(baseline_path) == collect_public_api(tmp_path)
    assert check_public_api(tmp_path, baseline=load_baseline(baseline_path)) == []


def test_repo_surface_matches_committed_baseline() -> None:
    violations = check_public_api(REPO_ROOT, baseline=load_baseline(BASELINE_PATH))

    assert violations == [], "\n".join(violations)
