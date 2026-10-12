"""Item 10: sanitised child environment and full argv[0] validation."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

from baselith.core.mcp.client import MCPClient
from baselith.core.mcp.stdio_client_transport import (
    build_child_env,
    resolve_command,
    validate_command,
)

STDIO = str(Path(__file__).parent / "fakes" / "stdio_server.py")


async def test_child_does_not_see_parent_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SECRET_PARENT_VAR", "hunter2")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-parent")
    client = MCPClient(command=[sys.executable, STDIO])
    await client.connect(env={"FAKE_MODE": "envdump", "SPEC_VAR": "1"})
    try:
        names = await client.call_tool("x", {})
    finally:
        await client.disconnect()
    assert "SECRET_PARENT_VAR" not in names
    assert "OPENAI_API_KEY" not in names
    assert "SPEC_VAR" in names and "FAKE_MODE" in names
    assert "PATH" in names


def test_build_child_env_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "PATH",
        "HOME",
        "LANG",
        "LC_ALL",
        "TMPDIR",
        "AWS_SECRET_ACCESS_KEY",
        "DATABASE_URL",
    ):
        monkeypatch.setenv(key, "v")
    env = build_child_env({"MINE": "1"})
    assert {"PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "MINE"} <= set(env)
    assert "AWS_SECRET_ACCESS_KEY" not in env and "DATABASE_URL" not in env
    assert env["PYTHONUNBUFFERED"] == "1"


def test_spec_env_overrides_the_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    # Was HOME; HOME steers user-site and rc-file loading, so a spec may no
    # longer set it (test_stdio_allowlist_escape.py). LANG is harmless.
    monkeypatch.setenv("LANG", "parent")
    assert build_child_env({"LANG": "spec"})["LANG"] == "spec"


def test_passthrough_knob(monkeypatch: pytest.MonkeyPatch, mcp_env: Any) -> None:
    monkeypatch.setenv("CORP_PROXY", "p")
    monkeypatch.setenv("CORP_CA_BUNDLE", "c")
    monkeypatch.setenv("NOT_LISTED", "n")
    mcp_env(MCP_STDIO_ENV_PASSTHROUGH="CORP_*,EXACT_ONE")
    env = build_child_env(None)
    assert "CORP_PROXY" in env and "CORP_CA_BUNDLE" in env and "NOT_LISTED" not in env


def _fake_bin(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_bare_allowlisted_name_resolves_in_trusted_dirs() -> None:
    # A bare name is found in the trusted bin dirs (the running interpreter's
    # is always one), never on PATH. Scripts must be absolute and approved.
    resolved = resolve_command(None, ["python3", STDIO, "x"])
    assert os.path.isabs(resolved[0]) and resolved[1:] == [STDIO, "x"]


def test_allowlisted_name_missing_from_trusted_dirs_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mcp_env: Any
) -> None:
    # Was "missing from PATH": being on PATH no longer counts for anything.
    _fake_bin(tmp_path, "node")
    mcp_env(MCP_STDIO_TRUSTED_BIN_DIRS=str(tmp_path / "empty"))
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(ValueError, match="(?i)not found|trusted"):
        resolve_command(None, ["node", STDIO])


def test_relative_paths_are_refused() -> None:
    for argv0 in ("./python", "bin/python", "../python"):
        with pytest.raises(ValueError, match="(?i)absolute|bare"):
            validate_command([argv0, "x"])


def test_absolute_path_must_exist_and_be_allowlisted(
    tmp_path: Path, mcp_env: Any
) -> None:
    mcp_env(MCP_STDIO_TRUSTED_BIN_DIRS=str(tmp_path))
    with pytest.raises(ValueError):
        validate_command([str(tmp_path / "python"), STDIO])  # does not exist
    other = _fake_bin(tmp_path, "evilbinary")
    with pytest.raises(ValueError, match="allowed"):
        validate_command([str(other)])
    ok = _fake_bin(tmp_path, "python3")
    validate_command([str(ok), STDIO])


def test_shell_is_never_allowed() -> None:
    with pytest.raises(ValueError):
        validate_command(["sh", "-c", "echo hi"])
    with pytest.raises(ValueError):
        validate_command(["/bin/bash", "-c", "echo hi"])


@pytest.mark.parametrize(
    "argv",
    [
        ["npx", "-y", "@scope/pkg"],
        ["uvx", "pkg"],
        ["bunx", "pkg"],
        ["uv", "tool", "run", "pkg"],
        ["bun", "x", "pkg"],
        ["deno", "run", "npm:pkg"],
    ],
)
def test_package_runners_need_an_explicit_opt_in(
    argv: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mcp_env: Any
) -> None:
    for name in ("npx", "uvx", "bunx", "uv", "bun", "deno"):
        _fake_bin(tmp_path, name)
    mcp_env(MCP_STDIO_TRUSTED_BIN_DIRS=str(tmp_path), MCP_ALLOW_PACKAGE_RUNNERS="false")
    with pytest.raises(ValueError, match="MCP_ALLOW_PACKAGE_RUNNERS"):
        validate_command(argv)
    mcp_env(MCP_STDIO_TRUSTED_BIN_DIRS=str(tmp_path), MCP_ALLOW_PACKAGE_RUNNERS="true")
    validate_command(argv)


def test_plain_uv_run_and_deno_local_are_not_runners(
    tmp_path: Path, mcp_env: Any
) -> None:
    for name in ("uv", "deno", "bun"):
        _fake_bin(tmp_path, name)
    mcp_env(MCP_STDIO_TRUSTED_BIN_DIRS=str(tmp_path))
    validate_command(["uv", "run", STDIO])
    validate_command(["deno", "run", STDIO])
    validate_command(["bun", "run", STDIO])


def test_current_interpreter_is_still_allowed_but_no_longer_unchecked() -> None:
    # It used to skip validation entirely (`python -c` rode through). It now
    # takes the same argument checks as any other python.
    validate_command([sys.executable, STDIO])
    with pytest.raises(ValueError):
        validate_command([sys.executable, "-c", "print(1)"])
