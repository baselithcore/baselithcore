"""Tests for bundled skill script enumeration and sandboxed execution."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.plugins.declarative import DeclarativeSkillLoader, SkillSandboxError
from core.plugins.skill_scripts import (
    RUN_SKILL_SCRIPT_TOOL_NAME,
    SkillScriptResult,
    make_run_skill_script_tool,
    run_skill_script,
)

JSON_SCRIPT = (
    "import json, sys\nprint(json.dumps({'ok': True, 'args': sys.argv[1:]}))\n"
)


def _make_skill(root: Path, slug: str = "demo") -> Path:
    skill_dir = root / slug
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {slug}\ndescription: A demo skill.\n---\n\nBody.\n",
        encoding="utf-8",
    )
    return skill_dir


def _add_script(skill_dir: Path, name: str, source: str) -> Path:
    path = skill_dir / "scripts" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


@pytest.fixture
def skill_root(tmp_path: Path) -> Path:
    root = tmp_path / "skills"
    root.mkdir()
    return root


class TestLoadedSkillBundles:
    def test_enumerates_bundled_subdirs(self, skill_root: Path) -> None:
        skill_dir = _make_skill(skill_root)
        _add_script(skill_dir, "hello.py", "print('hi')\n")
        _add_script(skill_dir, "nested/util.py", "print('util')\n")
        (skill_dir / "references").mkdir()
        (skill_dir / "references" / "notes.md").write_text("notes", encoding="utf-8")
        (skill_dir / "assets").mkdir()
        (skill_dir / "assets" / "logo.txt").write_text("logo", encoding="utf-8")
        loader = DeclarativeSkillLoader([skill_root])
        loaded = loader.activate(skill_dir / "SKILL.md")
        assert loaded.scripts == ["hello.py", "nested/util.py"]
        assert loaded.references == ["notes.md"]
        assert loaded.assets == ["logo.txt"]

    def test_empty_lists_when_dirs_absent(self, skill_root: Path) -> None:
        skill_dir = _make_skill(skill_root)
        loaded = DeclarativeSkillLoader([skill_root]).activate(skill_dir / "SKILL.md")
        assert loaded.scripts == []
        assert loaded.references == []
        assert loaded.assets == []

    def test_symlink_escaping_the_root_raises(
        self, skill_root: Path, tmp_path: Path
    ) -> None:
        skill_dir = _make_skill(skill_root)
        outside = tmp_path / "outside.py"
        outside.write_text("print('evil')\n", encoding="utf-8")
        scripts = skill_dir / "scripts"
        scripts.mkdir()
        (scripts / "link.py").symlink_to(outside)
        loader = DeclarativeSkillLoader([skill_root])
        with pytest.raises(SkillSandboxError):
            loader.activate(skill_dir / "SKILL.md")


class TestRunSkillScript:
    async def test_json_stdout_is_parsed(self, skill_root: Path) -> None:
        skill_dir = _make_skill(skill_root)
        _add_script(skill_dir, "emit.py", JSON_SCRIPT)
        loader = DeclarativeSkillLoader([skill_root])
        result = await run_skill_script(loader, "demo", "emit.py", ["a", "b"])
        assert isinstance(result, SkillScriptResult)
        assert result.exit_code == 0
        assert result.parsed_json == {"ok": True, "args": ["a", "b"]}

    async def test_non_json_stdout_gives_none(self, skill_root: Path) -> None:
        skill_dir = _make_skill(skill_root)
        _add_script(skill_dir, "plain.py", "print('hello world')\n")
        loader = DeclarativeSkillLoader([skill_root])
        result = await run_skill_script(loader, "demo", "plain.py", [])
        assert result.exit_code == 0
        assert "hello world" in result.stdout
        assert result.parsed_json is None

    async def test_non_zero_exit_surfaced(self, skill_root: Path) -> None:
        skill_dir = _make_skill(skill_root)
        _add_script(
            skill_dir, "boom.py", "import sys\nsys.stderr.write('boom!')\nsys.exit(3)\n"
        )
        loader = DeclarativeSkillLoader([skill_root])
        result = await run_skill_script(loader, "demo", "boom.py", [])
        assert result.exit_code == 3
        assert "boom!" in result.stderr

    async def test_timeout_kills_the_script(self, skill_root: Path) -> None:
        skill_dir = _make_skill(skill_root)
        _add_script(skill_dir, "sleepy.py", "import time\ntime.sleep(30)\n")
        loader = DeclarativeSkillLoader([skill_root])
        result = await run_skill_script(loader, "demo", "sleepy.py", [], timeout_s=0.5)
        assert result.exit_code != 0
        assert "timeout" in result.stderr.lower()

    async def test_stdout_truncated_at_cap(self, skill_root: Path) -> None:
        skill_dir = _make_skill(skill_root)
        _add_script(
            skill_dir, "big.py", "import sys\nsys.stdout.write('x' * 200_000)\n"
        )
        loader = DeclarativeSkillLoader([skill_root])
        result = await run_skill_script(loader, "demo", "big.py", [])
        assert len(result.stdout) <= 64 * 1024 + 64  # cap plus marker
        assert "truncated" in result.stdout

    async def test_traversal_rejected(self, skill_root: Path) -> None:
        skill_dir = _make_skill(skill_root)
        (skill_dir / "scripts").mkdir()
        loader = DeclarativeSkillLoader([skill_root])
        with pytest.raises(SkillSandboxError):
            await run_skill_script(loader, "demo", "../evil.py", [])

    async def test_absolute_path_rejected(
        self, skill_root: Path, tmp_path: Path
    ) -> None:
        skill_dir = _make_skill(skill_root)
        _add_script(skill_dir, "ok.py", "print('ok')\n")
        outside = tmp_path / "abs.py"
        outside.write_text("print('abs')\n", encoding="utf-8")
        loader = DeclarativeSkillLoader([skill_root])
        with pytest.raises(SkillSandboxError):
            await run_skill_script(loader, "demo", str(outside), [])

    async def test_other_extension_rejected(self, skill_root: Path) -> None:
        skill_dir = _make_skill(skill_root)
        _add_script(skill_dir, "run.sh", "echo hi\n")
        loader = DeclarativeSkillLoader([skill_root])
        with pytest.raises(ValueError, match="\\.py"):
            await run_skill_script(loader, "demo", "run.sh", [])

    async def test_symlink_escape_rejected(
        self, skill_root: Path, tmp_path: Path
    ) -> None:
        skill_dir = _make_skill(skill_root)
        outside = tmp_path / "outside.py"
        outside.write_text("print('evil')\n", encoding="utf-8")
        scripts = skill_dir / "scripts"
        scripts.mkdir()
        (scripts / "link.py").symlink_to(outside)
        loader = DeclarativeSkillLoader([skill_root])
        with pytest.raises(SkillSandboxError):
            await run_skill_script(loader, "demo", "link.py", [])

    async def test_unknown_skill_rejected(self, skill_root: Path) -> None:
        _make_skill(skill_root)
        loader = DeclarativeSkillLoader([skill_root])
        with pytest.raises(ValueError, match="nope"):
            await run_skill_script(loader, "nope", "x.py", [])


class TestRunSkillScriptTool:
    def test_tool_definition_shape(self, skill_root: Path) -> None:
        _make_skill(skill_root)
        tool = make_run_skill_script_tool(DeclarativeSkillLoader([skill_root]))
        assert tool.name == RUN_SKILL_SCRIPT_TOOL_NAME == "run_skill_script"
        assert tool.category == "mutating"
        assert callable(tool.fn)
        assert tool.parameters is not None
        assert tool.parameters["required"] == ["skill", "script"]

    async def test_tool_fn_runs_and_reports(self, skill_root: Path) -> None:
        skill_dir = _make_skill(skill_root)
        _add_script(skill_dir, "emit.py", JSON_SCRIPT)
        tool = make_run_skill_script_tool(DeclarativeSkillLoader([skill_root]))
        text = await tool.fn(skill="demo", script="emit.py", args=["x"])
        payload = json.loads(text)
        assert payload["exit_code"] == 0
        assert payload["parsed_json"] == {"ok": True, "args": ["x"]}

    async def test_tool_fn_surfaces_errors_as_text(self, skill_root: Path) -> None:
        _make_skill(skill_root)
        tool = make_run_skill_script_tool(DeclarativeSkillLoader([skill_root]))
        assert (await tool.fn(skill="demo", script="../evil.py")).startswith("Error:")
        assert (await tool.fn()).startswith("Error:")
