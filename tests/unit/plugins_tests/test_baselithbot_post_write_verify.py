"""Baselithbot post-write verification of Python files.

A successful ``fs_write`` of a ``.py`` file is followed by a stdlib
``py_compile`` check: a syntax error appends a ``verification: compile
failed: ...`` marker to the tool result while KEEPING the broken file on
disk — the marker is the agent's feedback loop for fixing it. Non-Python
files and the disabled gate keep the pre-existing result shape. The tool
surface also dispatches a post event on the core ToolHookRegistry.
"""

from __future__ import annotations

import pytest

from core.orchestration.hooks import (
    ToolHookEvent,
    get_tool_hook_registry,
    reset_tool_hook_registry,
)
from plugins.baselithbot.computer_use.config import AuditLogger, ComputerUseConfig
from plugins.baselithbot.computer_use.filesystem import ScopedFileSystem

_BROKEN_PY = "def f(:\n    return 1\n"
_VALID_PY = "def f() -> int:\n    return 1\n"


@pytest.fixture(autouse=True)
def _fresh_hook_registry():
    reset_tool_hook_registry()
    yield
    reset_tool_hook_registry()


def _fs(tmp_path, **config_overrides) -> ScopedFileSystem:
    root = tmp_path / "scope"
    root.mkdir(exist_ok=True)
    cfg = ComputerUseConfig(
        enabled=True,
        allow_filesystem=True,
        filesystem_root=str(root),
        **config_overrides,
    )
    return ScopedFileSystem(cfg, AuditLogger(None))


async def test_python_syntax_error_appends_marker_and_keeps_file(tmp_path) -> None:
    fs = _fs(tmp_path)

    out = await fs.write("broken.py", _BROKEN_PY)

    assert out["bytes_written"] == len(_BROKEN_PY.encode())
    assert out["verification"].startswith("compile failed:")
    assert "broken.py" in out["verification"] or "line" in out["verification"]
    # The file must be kept — the agent needs the error to fix it.
    kept = tmp_path / "scope" / "broken.py"
    assert kept.read_text() == _BROKEN_PY


async def test_valid_python_gets_ok_verification(tmp_path) -> None:
    fs = _fs(tmp_path)

    out = await fs.write("fine.py", _VALID_PY)

    assert out["verification"] == "ok"


async def test_non_python_file_unchanged(tmp_path) -> None:
    fs = _fs(tmp_path)

    out = await fs.write("notes.txt", "def f(: not python, no problem")

    assert "verification" not in out


async def test_config_flag_off_restores_old_behavior(tmp_path) -> None:
    fs = _fs(tmp_path, post_write_verify=False)

    out = await fs.write("broken.py", _BROKEN_PY)

    assert "verification" not in out
    assert (tmp_path / "scope" / "broken.py").read_text() == _BROKEN_PY


async def test_env_flag_off_changes_config_default(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BASELITH_POST_WRITE_VERIFY", "false")

    cfg = ComputerUseConfig(enabled=True, allow_filesystem=True)
    assert cfg.post_write_verify is False

    monkeypatch.setenv("BASELITH_POST_WRITE_VERIFY", "true")
    assert ComputerUseConfig().post_write_verify is True


def test_default_is_on() -> None:
    assert ComputerUseConfig().post_write_verify is True


async def test_tool_surface_registers_and_dispatches_post_hook(tmp_path) -> None:
    from plugins.baselithbot.computer_use.tools import build_computer_tool_definitions

    root = tmp_path / "scope"
    root.mkdir()
    cfg = ComputerUseConfig(
        enabled=True, allow_filesystem=True, filesystem_root=str(root)
    )
    tools = build_computer_tool_definitions(cfg)
    by_name = {t["name"]: t for t in tools}

    registry = get_tool_hook_registry()
    # Building the tool surface registered the logging post-hook.
    assert registry._matching("post", "baselithbot_fs_write")

    seen: list[ToolHookEvent] = []

    async def recorder(event: ToolHookEvent) -> None:
        seen.append(event)

    registry.register("post", "baselithbot_fs_write", recorder)

    out = await by_name["baselithbot_fs_write"]["handler"](
        path="broken.py", content=_BROKEN_PY
    )

    assert out["status"] == "success"
    assert out["verification"].startswith("compile failed:")
    assert len(seen) == 1
    event = seen[0]
    assert event.phase == "post"
    assert event.tool_name == "baselithbot_fs_write"
    assert event.metadata["verification"].startswith("compile failed:")
