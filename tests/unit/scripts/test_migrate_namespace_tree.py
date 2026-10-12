"""End-to-end runs of the namespace codemod over a mini repository."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("libcst")

from scripts.migrate_namespace import main
from scripts.namespace_migration.engine import run

GIT = shutil.which("git") or "git"

FILES: dict[str, str] = {
    "core/__init__.py": "",
    "core/_version.py": '__version__ = "1.0.0"\n',
    "core/cli/__init__.py": "",
    "core/cli/__main__.py": (
        "from pathlib import Path\n"
        "from core._version import __version__\n"
        "ROOT = Path(__file__).resolve().parents[2]\n"
        'TEMPLATES = ROOT / "templates"\n'
        "def main():\n    return __version__\n"
    ),
    "core/plugins/__init__.py": "",
    "core/plugins/loader.py": (
        "import sys, types\n"
        'def fqn(name):\n    return f"plugins.{name}"\n'
        'if "plugins" not in sys.modules:\n'
        '    sys.modules["plugins"] = types.ModuleType("plugins")\n'
    ),
    "plugins/__init__.py": "",
    "plugins/goals/__init__.py": "",
    "plugins/goals/plugin.py": "from core.plugins import loader\n",
    "plugins/goals/manifest.yaml": "name: goals\n",
    "baselith/__init__.py": 'from core._version import __version__\n_EXPORTS = {"x": "core.cli"}\n',
    "scripts/check.py": (
        "from pathlib import Path\n"
        "ROOT = Path(__file__).resolve().parents[1]\n"
        'CORE = ROOT / "core"\n'
        'LEGACY = ("core/cli/__main__.py",)\n'
    ),
    "tests/test_cli.py": (
        "from unittest import mock\n"
        "import core.cli.__main__\n"
        'def test_main():\n    with mock.patch("core._version.__version__", "x"):\n'
        "        assert core.cli.__main__.main()\n"
    ),
    "pyproject.toml": (
        '[project.scripts]\nbaselith = "core.cli.__main__:main"\n'
        '[tool.setuptools.packages.find]\ninclude = ["baselith*", "core*", "plugins*"]\n'
    ),
    "docs/guide.md": "Use `from core.cli import x`.\n\n```python\nimport core.cli\n```\n",
    "CHANGELOG.md": "- moved core/cli\n",
    "vendor/src/core/__init__.py": "",
    "vendor/src/core/engine.py": "",
    "vendor/src/app.py": "from core.engine import x\n",
}


def make_tree(root: Path, *, git: bool) -> Path:
    for rel, text in FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    if git:
        cmds = [
            ["init", "-q"],
            ["add", "-A"],
            [
                "-c",
                "commit.gpgsign=false",
                "-c",
                "user.email=t@t",
                "-c",
                "user.name=t",
                "commit",
                "-qm",
                "init",
            ],
        ]
        for cmd in cmds:
            subprocess.run(
                [GIT, "-C", str(root), *cmd], check=True, capture_output=True
            )
    return root


def snapshot(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): p.read_text(encoding="utf-8")
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".git" not in p.parts and "__pycache__" not in p.parts
    }


@pytest.fixture(params=[True, False], ids=["git", "plain"])
def tree(request: pytest.FixtureRequest, tmp_path: Path) -> Path:
    if request.param and shutil.which("git") is None:
        pytest.skip("git not available")
    return make_tree(tmp_path / "repo", git=request.param)


def test_dry_run_writes_nothing(tree: Path) -> None:
    before = snapshot(tree)
    report = run(tree, apply=False)
    assert snapshot(tree) == before
    assert report.counts["import_from"] >= 3
    assert len(report.moved) == 2


def test_apply_moves_and_rewrites(tree: Path) -> None:
    run(tree, apply=True)
    assert not (tree / "core").exists()
    assert not (tree / "plugins").exists()
    main_py = (tree / "baselith/core/cli/__main__.py").read_text()
    assert "from baselith.core._version import __version__" in main_py
    assert "parents[3]" in main_py
    loader = (tree / "baselith/core/plugins/loader.py").read_text()
    assert 'f"baselith.plugins.{name}"' in loader
    assert loader.count('"baselith.plugins"') == 3
    assert (
        "from baselith.core.plugins import loader"
        in (tree / "baselith/plugins/goals/plugin.py").read_text()
    )
    script = (tree / "scripts/check.py").read_text()
    assert 'ROOT / "baselith" / "core"' in script
    assert '"baselith/core/cli/__main__.py"' in script
    test = (tree / "tests/test_cli.py").read_text()
    assert "import baselith.core.cli.__main__" in test
    assert "baselith.core.cli.__main__.main()" in test
    assert '"baselith.core._version.__version__"' in test
    py = (tree / "pyproject.toml").read_text()
    assert 'include = ["baselith*"]' in py and '"baselith.core.cli.__main__:main"' in py
    assert "import baselith.core.cli" in (tree / "docs/guide.md").read_text()
    assert (tree / "CHANGELOG.md").read_text() == FILES["CHANGELOG.md"]
    assert (tree / "vendor/src/app.py").read_text() == FILES["vendor/src/app.py"]
    facade = (tree / "baselith/__init__.py").read_text()
    assert "from baselith.core._version" in facade and '"baselith.core.cli"' in facade


def test_second_run_is_noop(tree: Path) -> None:
    run(tree, apply=True)
    after_first = snapshot(tree)
    report = run(tree, apply=True)
    assert snapshot(tree) == after_first
    assert report.files_changed == {}
    assert report.moved == []


def test_check_fails_before_and_passes_after(
    tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--check", "--root", str(tree), "--quiet", "--jobs", "1"]) == 1
    assert main(["--apply", "--root", str(tree), "--quiet", "--jobs", "1"]) == 0
    assert main(["--check", "--root", str(tree), "--quiet", "--jobs", "1"]) == 0
    assert "namespace check OK" in capsys.readouterr().out


def test_check_catches_a_reintroduced_import(tree: Path) -> None:
    run(tree, apply=True)
    (tree / "scripts/new.py").write_text("from core.cli import x\n")
    assert main(["--check", "--root", str(tree), "--quiet", "--jobs", "1"]) == 1


def test_parallel_output_identical(tmp_path: Path) -> None:
    a = make_tree(tmp_path / "a", git=False)
    b = make_tree(tmp_path / "b", git=False)
    for i in range(60):  # enough files to take the process-pool path
        for root in (a, b):
            (root / f"tests/test_gen_{i}.py").write_text("from core.cli import x\n")
    run(a, apply=True, jobs=1)
    run(b, apply=True, jobs=3)
    assert snapshot(a) == snapshot(b)


def test_json_report(tree: Path, tmp_path: Path) -> None:
    out = tmp_path / "r.json"
    assert (
        main(
            [
                "--dry-run",
                "--root",
                str(tree),
                "--json",
                str(out),
                "--quiet",
                "--jobs",
                "1",
            ]
        )
        == 0
    )
    data = json.loads(out.read_text())
    assert data["counts"]["import_from"] >= 3
    assert data["moves"] == ["core/ -> baselith/core/", "plugins/ -> baselith/plugins/"]


def test_skip_file_pragma(tree: Path) -> None:
    (tree / "scripts/legacy.py").write_text(
        "# ns-migrate: skip-file\nfrom core.cli import x\n"
    )
    run(tree, apply=True)
    assert "from core.cli import x" in (tree / "scripts/legacy.py").read_text()


def test_untracked_leftovers_follow_the_move(tree: Path) -> None:
    (tree / "plugins/goals/.env").write_text("SECRET=1\n")
    run(tree, apply=True)
    assert (tree / "baselith/plugins/goals/.env").read_text() == "SECRET=1\n"
    assert not (tree / "plugins").exists()


def test_ignored_leftovers_at_the_destination_do_not_nest_the_move(tree: Path) -> None:
    stale = tree / "baselith/core/__pycache__"
    stale.mkdir(parents=True)
    (stale / "x.cpython-312.pyc").write_bytes(b"")
    run(tree, apply=True)
    assert (tree / "baselith/core/cli/__main__.py").is_file()
    assert not (tree / "baselith/core/core").exists()
