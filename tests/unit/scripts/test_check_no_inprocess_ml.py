"""scripts/check_no_inprocess_ml.py: flags calls, honours a shrink-only allowlist."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "check_no_inprocess_ml",
    Path(__file__).resolve().parents[3] / "scripts" / "check_no_inprocess_ml.py",
)
assert _SPEC and _SPEC.loader
guard = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = guard  # dataclasses resolve their module by name
_SPEC.loader.exec_module(guard)


def _write(root: Path, rel: str, body: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _allow(root: Path, *entries: tuple[str, str]) -> Path:
    lines = ["allow:"] + [f"  - path: {p}\n    reason: {r}" for p, r in entries]
    path = root / "allow.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "call",
    [
        "SentenceTransformer('m')",
        "sentence_transformers.CrossEncoder('m')",
        "BGEM3FlagModel('m')",
        "FlagReranker('m')",
        "DocumentConverter()",
        "QdrantClient(path='x')",
        "qdrant_client.AsyncQdrantClient(url='u')",
    ],
)
def test_each_forbidden_constructor_is_flagged(tmp_path: Path, call: str) -> None:
    _write(tmp_path, "plugins/p/mod.py", f"x = {call}\n")
    problems = guard.check(tmp_path, tmp_path / "none.yaml")
    assert len(problems) == 1 and "plugins/p/mod.py:1" in problems[0]


def test_mentions_imports_tests_and_build_dirs_are_ignored(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "plugins/p/a.py",
        '"""Uses SentenceTransformer( in prose."""\nfrom x import QdrantClient\n',
    )
    _write(tmp_path, "plugins/p/tests/test_a.py", "QdrantClient(':memory:')\n")
    _write(tmp_path, "plugins/p/ui/node_modules/m.py", "CrossEncoder('m')\n")
    assert guard.check(tmp_path, tmp_path / "none.yaml") == []


def test_allowlist_exempts_with_reason_and_goes_stale(tmp_path: Path) -> None:
    _write(tmp_path, "plugins/v/vendor/e.py", "SentenceTransformer('m')\n")
    allow = _allow(tmp_path, ("plugins/v/vendor", "vendored, not migrated"))
    assert guard.check(tmp_path, allow) == []

    _write(tmp_path, "plugins/v/vendor/e.py", "x = 1\n")  # migrated, entry now stale
    assert "no longer contains a flagged call" in guard.check(tmp_path, allow)[0]

    allow = _allow(tmp_path, ("plugins/gone", "x"))
    assert "no longer exists" in guard.check(tmp_path, allow)[0]


def test_allowlist_entry_without_reason_is_rejected(tmp_path: Path) -> None:
    allow = tmp_path / "allow.yaml"
    allow.write_text("allow:\n  - path: plugins/x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="reason"):
        guard.load_allowlist(allow)


def test_real_repo_is_green() -> None:
    assert guard.check() == []
