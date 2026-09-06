"""Tests for the silent-exception ratchet (scripts/check_exception_hygiene.py)."""

from __future__ import annotations

import textwrap
from pathlib import Path

from scripts.check_exception_hygiene import (
    BASELINE_PATH,
    REPO_ROOT,
    check_exception_hygiene,
    find_silent_handlers,
    load_baseline,
    scan_tree,
    write_baseline,
)

SAMPLE = textwrap.dedent(
    """
    import logging

    log = logging.getLogger()


    def swallowed():
        try:
            pass
        except Exception:
            pass


    def logged():
        try:
            pass
        except Exception as exc:
            log.debug("x: %s", exc)


    def narrow():
        try:
            pass
        except ValueError:
            pass


    def bare_return():
        try:
            pass
        except:  # noqa: E722
            return None


    def reraised():
        try:
            pass
        except BaseException:
            raise


    def documented():
        try:
            pass
        except Exception:  # silent-ok: best-effort cache warmup
            pass


    def builtins_qualified():
        try:
            pass
        except builtins.Exception:
            x = 1
    """
)


def test_detects_only_silent_broad_handlers(tmp_path: Path) -> None:
    module = tmp_path / "m.py"
    module.write_text(SAMPLE, encoding="utf-8")

    found = find_silent_handlers(module)

    lines = SAMPLE.splitlines()
    expected = [
        (lines.index("    except Exception:") + 1, "Exception"),
        (lines.index("    except:  # noqa: E722") + 1, "bare"),
        (lines.index("    except builtins.Exception:") + 1, "Exception"),
    ]
    assert [(h.line, h.kind) for h in found] == expected


def test_scan_tree_counts_per_file(tmp_path: Path) -> None:
    (tmp_path / "core" / "pkg").mkdir(parents=True)
    (tmp_path / "core" / "pkg" / "a.py").write_text(SAMPLE, encoding="utf-8")
    (tmp_path / "core" / "pkg" / "clean.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "core" / "__pycache__").mkdir()
    (tmp_path / "core" / "__pycache__" / "junk.py").write_text(SAMPLE, encoding="utf-8")

    assert scan_tree(tmp_path) == {"core/pkg/a.py": 3}


def test_new_silent_handler_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "a.py").write_text(SAMPLE, encoding="utf-8")

    violations = check_exception_hygiene(tmp_path, baseline={})

    assert len(violations) == 1
    assert "core/a.py" in violations[0]
    assert "3 silent" in violations[0]


def test_baselined_count_may_shrink_but_not_grow(tmp_path: Path) -> None:
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "a.py").write_text(SAMPLE, encoding="utf-8")

    assert check_exception_hygiene(tmp_path, baseline={"core/a.py": 3}) == []
    assert check_exception_hygiene(tmp_path, baseline={"core/a.py": 5}) == []
    grew = check_exception_hygiene(tmp_path, baseline={"core/a.py": 2})
    assert len(grew) == 1 and "grew" in grew[0]


def test_clean_file_must_leave_the_baseline(tmp_path: Path) -> None:
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "a.py").write_text("x = 1\n", encoding="utf-8")

    violations = check_exception_hygiene(tmp_path, baseline={"core/a.py": 2})

    assert len(violations) == 1
    assert "remove it" in violations[0]


def test_write_and_load_baseline_roundtrip(tmp_path: Path) -> None:
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "a.py").write_text(SAMPLE, encoding="utf-8")
    baseline_path = tmp_path / "baseline.json"

    assert write_baseline(tmp_path, baseline_path) == 3
    assert load_baseline(baseline_path) == {"core/a.py": 3}


def test_repo_baseline_is_honest() -> None:
    """The committed baseline must match the tree: no new silent handler."""
    violations = check_exception_hygiene(
        REPO_ROOT, baseline=load_baseline(BASELINE_PATH)
    )

    assert violations == [], "\n".join(violations)
