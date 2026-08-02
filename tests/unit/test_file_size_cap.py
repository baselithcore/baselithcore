from __future__ import annotations

import json
from pathlib import Path

from scripts.check_file_size import (
    BASELINE_PATH,
    MAX_LINES,
    REPO_ROOT,
    check_file_sizes,
    count_lines,
    load_baseline,
    write_baseline,
)


def write_lines(path: Path, line_count: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x = 1\n" * line_count, encoding="utf-8")


def test_file_within_cap_passes(tmp_path: Path) -> None:
    write_lines(tmp_path / "core/services/small.py", 10)

    assert check_file_sizes(tmp_path, baseline={}) == []


def test_new_file_over_cap_is_rejected(tmp_path: Path) -> None:
    write_lines(tmp_path / "core/services/huge.py", MAX_LINES + 1)

    violations = check_file_sizes(tmp_path, baseline={})

    assert len(violations) == 1
    assert "core/services/huge.py" in violations[0]
    assert f"exceeds the {MAX_LINES}-line cap" in violations[0]


def test_baselined_file_may_stay_over_cap(tmp_path: Path) -> None:
    write_lines(tmp_path / "core/services/legacy.py", 620)

    violations = check_file_sizes(tmp_path, baseline={"core/services/legacy.py": 620})

    assert violations == []


def test_baselined_file_may_shrink(tmp_path: Path) -> None:
    write_lines(tmp_path / "core/services/legacy.py", 550)

    violations = check_file_sizes(tmp_path, baseline={"core/services/legacy.py": 620})

    assert violations == []


def test_baselined_file_may_not_grow(tmp_path: Path) -> None:
    write_lines(tmp_path / "core/services/legacy.py", 621)

    violations = check_file_sizes(tmp_path, baseline={"core/services/legacy.py": 620})

    assert len(violations) == 1
    assert "grew to 621 lines (frozen at 620)" in violations[0]


def test_baseline_entry_must_be_dropped_once_under_cap(tmp_path: Path) -> None:
    write_lines(tmp_path / "core/services/legacy.py", MAX_LINES)

    violations = check_file_sizes(tmp_path, baseline={"core/services/legacy.py": 620})

    assert len(violations) == 1
    assert "remove it from scripts/file_size_baseline.json" in violations[0]


def test_stale_baseline_entry_for_deleted_file_is_reported(tmp_path: Path) -> None:
    violations = check_file_sizes(tmp_path, baseline={"core/services/gone.py": 620})

    assert violations == [
        "core/services/gone.py: baselined file no longer exists; "
        "remove it from scripts/file_size_baseline.json"
    ]


def test_excluded_trees_are_ignored(tmp_path: Path) -> None:
    write_lines(tmp_path / "templates/project/app.py", 900)
    write_lines(tmp_path / "backstage-portal/src/index.ts", 900)
    write_lines(tmp_path / "plugins/demo/ui/node_modules/pkg/index.js", 900)
    write_lines(tmp_path / "plugins/demo/ui/dist/bundle.js", 900)
    write_lines(tmp_path / ".venv/lib/thing.py", 900)

    assert check_file_sizes(tmp_path, baseline={}) == []


def test_vendored_and_built_assets_are_ignored(tmp_path: Path) -> None:
    write_lines(tmp_path / "plugins/demo/vendor/app/config.py", 900)
    write_lines(tmp_path / "plugins/demo/static/assets/index-DFgg2bSS.js", 900)
    write_lines(tmp_path / "plugins/demo/ui/src/legacy.min.js", 900)

    assert check_file_sizes(tmp_path, baseline={}) == []


def test_hand_written_static_javascript_is_checked(tmp_path: Path) -> None:
    """`static/admin.js` is authored by hand — only `static/assets/` is build output."""
    write_lines(tmp_path / "core/static/admin.js", MAX_LINES + 1)

    violations = check_file_sizes(tmp_path, baseline={})

    assert len(violations) == 1
    assert "core/static/admin.js" in violations[0]


def test_non_source_suffixes_are_ignored(tmp_path: Path) -> None:
    long_text = "line\n" * 900
    (tmp_path / "docs").mkdir(parents=True)
    (tmp_path / "docs/reference.md").write_text(long_text, encoding="utf-8")
    (tmp_path / "data.json").write_text(long_text, encoding="utf-8")

    assert check_file_sizes(tmp_path, baseline={}) == []


def test_frontend_sources_are_checked(tmp_path: Path) -> None:
    write_lines(tmp_path / "plugins/demo/ui/src/App.tsx", MAX_LINES + 1)

    violations = check_file_sizes(tmp_path, baseline={})

    assert len(violations) == 1
    assert "plugins/demo/ui/src/App.tsx" in violations[0]


def test_count_lines_matches_wc_semantics(tmp_path: Path) -> None:
    empty = tmp_path / "empty.py"
    empty.write_text("", encoding="utf-8")
    assert count_lines(empty) == 0

    unterminated = tmp_path / "unterminated.py"
    unterminated.write_text("a\nb", encoding="utf-8")
    assert count_lines(unterminated) == 2

    # A form feed is not a line break for `wc -l`, so it must not be one here.
    form_feed = tmp_path / "form_feed.py"
    form_feed.write_text("a\x0cb\n", encoding="utf-8")
    assert count_lines(form_feed) == 1


def test_write_baseline_freezes_only_over_cap_files(tmp_path: Path) -> None:
    write_lines(tmp_path / "core/services/small.py", 10)
    write_lines(tmp_path / "core/services/huge.py", 700)
    baseline_path = tmp_path / "baseline.json"

    frozen_count = write_baseline(tmp_path, baseline_path)

    assert frozen_count == 1
    payload = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert payload["max_lines"] == MAX_LINES
    assert payload["files"] == {"core/services/huge.py": 700}
    assert check_file_sizes(tmp_path, baseline=load_baseline(baseline_path)) == []


def test_repository_satisfies_the_cap() -> None:
    """The committed baseline must match the real tree — no drift, no debt growth."""
    assert check_file_sizes(REPO_ROOT, baseline=load_baseline(BASELINE_PATH)) == []
