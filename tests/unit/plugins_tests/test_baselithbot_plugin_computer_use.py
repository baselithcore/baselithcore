"""Unit tests for the Baselithbot plugin — computer use, shell, filesystem, audit."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_computer_use_disabled_by_default_returns_denied() -> None:
    from plugins.baselithbot.computer_use.config import ComputerUseConfig
    from plugins.baselithbot.computer_use.tools import build_computer_tool_definitions

    tools = build_computer_tool_definitions(ComputerUseConfig())
    by_name = {t["name"]: t for t in tools}

    out = await by_name["baselithbot_mouse_click"]["handler"](x=10, y=20)
    assert out["status"] == "denied"
    assert "disabled" in out["error"].lower()


@pytest.mark.asyncio
async def test_computer_use_capability_flag_blocks_when_off() -> None:
    from plugins.baselithbot.computer_use.config import ComputerUseConfig
    from plugins.baselithbot.computer_use.tools import build_computer_tool_definitions

    cfg = ComputerUseConfig(enabled=True, allow_mouse=False, allow_keyboard=True)
    tools = build_computer_tool_definitions(cfg)
    by_name = {t["name"]: t for t in tools}

    out = await by_name["baselithbot_mouse_click"]["handler"](x=10, y=20)
    assert out["status"] == "denied"
    assert "mouse" in out["error"].lower()


@pytest.mark.asyncio
async def test_shell_executor_blocks_unallowlisted_command(tmp_path) -> None:
    from plugins.baselithbot.computer_use.config import (
        AuditLogger,
        ComputerUseConfig,
        ComputerUseError,
    )
    from plugins.baselithbot.computer_use.shell_exec import ShellExecutor

    cfg = ComputerUseConfig(
        enabled=True, allow_shell=True, allowed_shell_commands=["echo"]
    )
    audit = AuditLogger(str(tmp_path / "audit.log"))
    sh = ShellExecutor(cfg, audit)

    with pytest.raises(ComputerUseError, match="not in the allowlist"):
        await sh.run("rm -rf /tmp/x")


@pytest.mark.asyncio
async def test_shell_executor_rejects_shell_metacharacters(tmp_path) -> None:
    from plugins.baselithbot.computer_use.config import (
        AuditLogger,
        ComputerUseConfig,
        ComputerUseError,
    )
    from plugins.baselithbot.computer_use.shell_exec import ShellExecutor

    cfg = ComputerUseConfig(
        enabled=True,
        allow_shell=True,
        allowed_shell_commands=["ifconfig", "grep", "echo"],
    )
    audit = AuditLogger(str(tmp_path / "audit.log"))
    sh = ShellExecutor(cfg, audit)

    for bogus in (
        "ifconfig | grep inet",
        "echo hi; echo bye",
        "echo hi && echo bye",
        "echo hi > /tmp/out",
        "echo $(whoami)",
        "echo `whoami`",
    ):
        with pytest.raises(ComputerUseError, match="shell metacharacter"):
            await sh.run(bogus)


@pytest.mark.asyncio
async def test_shell_executor_runs_allowlisted_echo(tmp_path) -> None:
    from plugins.baselithbot.computer_use.config import AuditLogger, ComputerUseConfig
    from plugins.baselithbot.computer_use.shell_exec import ShellExecutor

    cfg = ComputerUseConfig(
        enabled=True, allow_shell=True, allowed_shell_commands=["echo"]
    )
    audit_path = tmp_path / "audit.log"
    audit = AuditLogger(str(audit_path))
    sh = ShellExecutor(cfg, audit)

    result = await sh.run("echo baselithbot")
    assert result["return_code"] == 0
    assert "baselithbot" in result["stdout"]
    audit.flush()
    assert audit_path.is_file()
    assert audit_path.read_text().count("\n") >= 1


@pytest.mark.asyncio
async def test_filesystem_blocks_path_escape(tmp_path) -> None:
    from plugins.baselithbot.computer_use.config import (
        AuditLogger,
        ComputerUseConfig,
        ComputerUseError,
    )
    from plugins.baselithbot.computer_use.filesystem import ScopedFileSystem

    root = tmp_path / "scope"
    root.mkdir()
    cfg = ComputerUseConfig(
        enabled=True, allow_filesystem=True, filesystem_root=str(root)
    )
    fs = ScopedFileSystem(cfg, AuditLogger(None))

    with pytest.raises(ComputerUseError, match="escapes filesystem_root"):
        await fs.read("../etc/passwd")


@pytest.mark.asyncio
async def test_filesystem_round_trip(tmp_path) -> None:
    from plugins.baselithbot.computer_use.config import AuditLogger, ComputerUseConfig
    from plugins.baselithbot.computer_use.filesystem import ScopedFileSystem

    root = tmp_path / "scope"
    root.mkdir()
    cfg = ComputerUseConfig(
        enabled=True, allow_filesystem=True, filesystem_root=str(root)
    )
    fs = ScopedFileSystem(cfg, AuditLogger(None))

    write_out = await fs.write("notes/hello.txt", "ciao baselithbot")
    assert write_out["bytes_written"] == len(b"ciao baselithbot")

    read_out = await fs.read("notes/hello.txt")
    assert read_out["content"] == "ciao baselithbot"

    listing = await fs.list_dir("notes")
    names = {entry["name"] for entry in listing["entries"]}
    assert "hello.txt" in names


@pytest.mark.asyncio
async def test_os_controller_audit_records_actions(tmp_path) -> None:
    from plugins.baselithbot.computer_use.config import AuditLogger, ComputerUseConfig
    from plugins.baselithbot.computer_use.os_control import OSController

    audit_path = tmp_path / "audit.log"
    audit = AuditLogger(str(audit_path))
    cfg = ComputerUseConfig(enabled=True, allow_mouse=True, allow_keyboard=True)
    ctrl = OSController(cfg, audit)

    fake_pyautogui = MagicMock()
    fake_pyautogui.click = MagicMock()
    fake_pyautogui.typewrite = MagicMock()
    fake_pyautogui.hotkey = MagicMock()

    with patch(
        "plugins.baselithbot.computer_use.os_control._load_pyautogui",
        return_value=fake_pyautogui,
    ):
        await ctrl.mouse_click(x=10, y=20, button="left", clicks=1)
        await ctrl.kbd_type("ciao", interval=0.0)
        await ctrl.kbd_hotkey("ctrl", "c")

    fake_pyautogui.click.assert_called_once()
    fake_pyautogui.typewrite.assert_called_once_with("ciao", 0.0)
    fake_pyautogui.hotkey.assert_called_once_with("ctrl", "c")

    audit.flush()
    log_lines = audit_path.read_text().strip().splitlines()
    actions = [json.loads(ln)["action"] for ln in log_lines]
    assert actions == ["mouse_click", "kbd_type", "kbd_hotkey"]


@pytest.mark.asyncio
async def test_desktop_agent_tracks_vision_tokens() -> None:
    """DesktopAgent must accumulate vision tokens and surface them on result."""
    from plugins.baselithbot.computer_use.config import ComputerUseConfig
    from plugins.baselithbot.desktop_agent import DesktopAgent

    class _StubVision:
        async def analyze(self, request):  # type: ignore[no-untyped-def]
            class _R:
                content = '{"tool": "done", "reasoning": "goal reached"}'
                raw_response = {"tool": "done", "reasoning": "goal reached"}
                as_json = {"tool": "done", "reasoning": "goal reached"}
                tokens_used = 42
                model = "claude-3.5-sonnet"
                provider = "anthropic"

            return _R()

    async def _screenshot_handler(**_kwargs):  # type: ignore[no-untyped-def]
        return {"status": "success", "screenshot_base64": "data"}

    tools = {
        "baselithbot_desktop_screenshot": {
            "name": "baselithbot_desktop_screenshot",
            "description": "stub",
            "input_schema": {"type": "object"},
            "handler": _screenshot_handler,
        }
    }
    policy = ComputerUseConfig(enabled=True, allow_screenshot=True)
    agent = DesktopAgent(vision=_StubVision(), tools=tools, policy=policy)  # type: ignore[arg-type]

    result = await agent.execute(goal="do nothing", max_steps=2)

    assert result.success is True
    assert result.tokens_used == 42
    assert result.model == "claude-3.5-sonnet"
    assert result.provider == "anthropic"


@pytest.mark.asyncio
async def test_audit_logger_batch_flush(tmp_path) -> None:
    from plugins.baselithbot.computer_use.config import AuditLogger

    audit_path = tmp_path / "audit.log"
    audit = AuditLogger(str(audit_path), batch_size=4, flush_interval_seconds=60.0)
    for i in range(3):
        audit.record("ping", n=i)
    assert not audit_path.exists() or audit_path.read_text() == ""
    audit.record("ping", n=3)
    # batch threshold reached -> flushed
    assert audit_path.is_file()
    assert audit_path.read_text().count("\n") == 4


def test_audit_logger_redacts_sensitive_keys(tmp_path) -> None:
    from plugins.baselithbot.computer_use.config import AuditLogger

    audit_path = tmp_path / "audit.log"
    audit = AuditLogger(str(audit_path), batch_size=1)
    audit.record("send", bot_token="should-be-hidden", target="user-1")
    contents = audit_path.read_text()
    assert "should-be-hidden" not in contents
    assert "<redacted>" in contents
    assert "user-1" in contents


@pytest.mark.asyncio
async def test_filesystem_rejects_symlink_escape(tmp_path) -> None:
    import os

    from plugins.baselithbot.computer_use.config import (
        AuditLogger,
        ComputerUseConfig,
        ComputerUseError,
    )
    from plugins.baselithbot.computer_use.filesystem import ScopedFileSystem

    root = tmp_path / "scope"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("oops")
    link = root / "leak"
    os.symlink(outside, link)

    cfg = ComputerUseConfig(
        enabled=True, allow_filesystem=True, filesystem_root=str(root)
    )
    fs = ScopedFileSystem(cfg, AuditLogger(None))
    with pytest.raises(ComputerUseError, match="escapes filesystem_root|symlink"):
        await fs.read("leak")


def test_filesystem_rejects_null_byte_in_path(tmp_path) -> None:
    from plugins.baselithbot.computer_use.config import (
        AuditLogger,
        ComputerUseConfig,
        ComputerUseError,
    )
    from plugins.baselithbot.computer_use.filesystem import ScopedFileSystem

    root = tmp_path / "scope"
    root.mkdir()
    cfg = ComputerUseConfig(
        enabled=True, allow_filesystem=True, filesystem_root=str(root)
    )
    fs = ScopedFileSystem(cfg, AuditLogger(None))
    with pytest.raises(ComputerUseError, match="null byte"):
        fs._resolve("nope\x00")


@pytest.mark.asyncio
async def test_desktop_vision_jpeg_format_validated() -> None:
    from plugins.baselithbot.computer_use.config import AuditLogger, ComputerUseConfig
    from plugins.baselithbot.computer_use.desktop_vision import DesktopVision

    cfg = ComputerUseConfig(enabled=True, allow_screenshot=True)
    vision = DesktopVision(cfg, AuditLogger(None))
    with pytest.raises(ValueError, match="unsupported image_format"):
        await vision.screenshot(image_format="GIF")
