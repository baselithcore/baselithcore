"""Stdio escape corpus 2/3: script location, argv[0] resolution, exact names, no shell.

Symlinks out of the allowed dir, world-writable files and dirs, PATH hijack,
non-exact names, denied shells/wrappers, fixed cwd.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

from core.mcp.stdio_client_transport import (
    resolve_command,
    spawn,
    validate_command,
)

from .stdio_support import POSIX
from .stdio_support import exe as _exe

# ------------------------------------------------- script location and modes


def test_relative_and_traversing_script_paths_refused(
    allowed: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(allowed)
    with pytest.raises(ValueError, match="(?i)absolute"):
        validate_command([sys.executable, "ok.py"])
    with pytest.raises(ValueError, match="(?i)absolute"):
        validate_command([sys.executable, "./ok.py"])
    outside = tmp_path / "outside.py"
    outside.write_text("pass\n")
    with pytest.raises(ValueError, match=r"(?i)\.\."):
        validate_command([sys.executable, f"{allowed}/../outside.py"])
    with pytest.raises(ValueError):
        validate_command([sys.executable, str(outside)])


@POSIX
def test_symlink_escaping_the_allowed_dir_refused(
    allowed: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.py"
    outside.write_text("import os\n")
    (allowed / "link.py").symlink_to(outside)
    with pytest.raises(ValueError, match="(?i)outside|allowed"):
        validate_command([sys.executable, str(allowed / "link.py")])
    # A symlink that stays inside is fine.
    (allowed / "inner.py").symlink_to(allowed / "ok.py")
    validate_command([sys.executable, str(allowed / "inner.py")])


@POSIX
def test_symlinked_directory_escaping_refused(allowed: Path, tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "s.py").write_text("pass\n")
    (allowed / "dir").symlink_to(elsewhere)
    with pytest.raises(ValueError):
        validate_command([sys.executable, str(allowed / "dir" / "s.py")])


@POSIX
def test_world_writable_script_refused(allowed: Path) -> None:
    script = allowed / "ww.py"
    script.write_text("pass\n")
    script.chmod(0o666)
    with pytest.raises(ValueError, match="(?i)world-writable"):
        validate_command([sys.executable, str(script)])


@POSIX
def test_script_in_world_writable_directory_refused(allowed: Path) -> None:
    sub = allowed / "sub"
    sub.mkdir()
    (sub / "s.py").write_text("pass\n")
    sub.chmod(0o777)
    try:
        with pytest.raises(ValueError, match="(?i)world-writable"):
            validate_command([sys.executable, str(sub / "s.py")])
    finally:
        sub.chmod(0o755)


@POSIX
def test_world_writable_allowed_dir_refused(allowed: Path) -> None:
    allowed.chmod(0o777)
    try:
        with pytest.raises(ValueError, match="(?i)world-writable"):
            validate_command([sys.executable, str(allowed / "ok.py")])
    finally:
        allowed.chmod(0o755)


def test_missing_script_and_directories_refused(allowed: Path) -> None:
    with pytest.raises(ValueError):
        validate_command([sys.executable, str(allowed / "nope.py")])
    (allowed / "pkgdir").mkdir()
    with pytest.raises(ValueError):
        validate_command([sys.executable, str(allowed / "pkgdir")])


def test_relative_allowed_dir_is_ignored(tmp_path: Path, mcp_env: Any) -> None:
    (tmp_path / "rel").mkdir()
    (tmp_path / "rel" / "s.py").write_text("pass\n")
    mcp_env(MCP_STDIO_ALLOWED_DIRS="rel")
    with pytest.raises(ValueError):
        validate_command([sys.executable, str(tmp_path / "rel" / "s.py")])


# ------------------------------------------- (2) argv[0] resolution via PATH


@POSIX
def test_path_cannot_hijack_a_bare_name(
    allowed: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evil = tmp_path / "evil"
    _exe(evil, "python3")
    monkeypatch.setenv("PATH", f"{evil}:{os.environ['PATH']}")
    try:
        resolved = resolve_command(None, ["python3", str(allowed / "ok.py")])
    except ValueError:
        return  # no trusted python3 on this machine: refusing is also safe
    assert not Path(resolved[0]).resolve().is_relative_to(evil.resolve())


@POSIX
def test_relative_path_element_cannot_hijack(
    allowed: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evil = tmp_path / "cwd-evil"
    _exe(evil, "python3")
    monkeypatch.chdir(evil)
    monkeypatch.setenv("PATH", f".:{os.environ['PATH']}")
    try:
        resolved = resolve_command(None, ["python3", str(allowed / "ok.py")])
    except ValueError:
        return
    assert not Path(resolved[0]).resolve().is_relative_to(evil.resolve())


def test_name_only_on_path_is_not_found(
    allowed: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mcp_env: Any
) -> None:
    only = tmp_path / "pathonly"
    _exe(only, "deno")
    mcp_env(
        MCP_STDIO_ALLOWED_DIRS=str(allowed),
        MCP_STDIO_TRUSTED_BIN_DIRS=str(tmp_path / "empty"),
    )
    monkeypatch.setenv("PATH", str(only))
    with pytest.raises(ValueError, match="(?i)trusted"):
        validate_command(["deno", "run", str(allowed / "ok.ts")])


@POSIX
def test_absolute_binary_outside_trusted_dirs_refused(
    allowed: Path, tmp_path: Path
) -> None:
    fake = _exe(tmp_path / "anywhere", "python3")
    with pytest.raises(ValueError, match="(?i)trusted"):
        validate_command([str(fake), str(allowed / "ok.py")])


@POSIX
def test_trusted_symlink_pointing_outside_refused(
    allowed: Path, tmp_path: Path, mcp_env: Any
) -> None:
    real = _exe(tmp_path / "untrusted", "realnode")
    bins = tmp_path / "bins"
    bins.mkdir()
    (bins / "node").symlink_to(real)
    mcp_env(MCP_STDIO_ALLOWED_DIRS=str(allowed), MCP_STDIO_TRUSTED_BIN_DIRS=str(bins))
    with pytest.raises(ValueError, match="(?i)outside|trusted"):
        validate_command(["node", str(allowed / "ok.js")])


@POSIX
def test_trusted_binary_that_is_a_symlink_to_a_shell_refused(
    allowed: Path, tmp_path: Path, mcp_env: Any
) -> None:
    sh = shutil.which("sh")
    assert sh
    bins = tmp_path / "bins"
    bins.mkdir()
    (bins / "python3").symlink_to(sh)
    mcp_env(
        MCP_STDIO_ALLOWED_DIRS=str(allowed),
        MCP_STDIO_TRUSTED_BIN_DIRS=f"{bins},{Path(sh).resolve().parent}",
    )
    with pytest.raises(ValueError, match="(?i)shell|wrapper|denied"):
        validate_command(["python3", str(allowed / "ok.py")])


@POSIX
def test_world_writable_binary_refused(
    allowed: Path, tmp_path: Path, mcp_env: Any
) -> None:
    bins = tmp_path / "bins"
    fake = _exe(bins, "python3")
    fake.chmod(0o777)
    mcp_env(MCP_STDIO_ALLOWED_DIRS=str(allowed), MCP_STDIO_TRUSTED_BIN_DIRS=str(bins))
    with pytest.raises(ValueError, match="(?i)world-writable"):
        validate_command(["python3", str(allowed / "ok.py")])


# ------------------------------------------------------ (3) exact matching


@POSIX
@pytest.mark.parametrize(
    "name", ["python3.12", "Python3", "PYTHON3", "python3x", "xpython3"]
)
def test_allowlist_matches_names_exactly(
    name: str, allowed: Path, tmp_path: Path, mcp_env: Any
) -> None:
    bins = tmp_path / "bins"
    _exe(bins, name)
    mcp_env(MCP_STDIO_ALLOWED_DIRS=str(allowed), MCP_STDIO_TRUSTED_BIN_DIRS=str(bins))
    with pytest.raises(ValueError, match="not in the allowed command"):
        validate_command([name, str(allowed / "ok.py")])
    with pytest.raises(ValueError, match="not in the allowed command"):
        validate_command([str(bins / name), str(allowed / "ok.py")])


@POSIX
def test_listing_a_variant_name_allows_exactly_that_name(
    allowed: Path, tmp_path: Path, mcp_env: Any
) -> None:
    bins = tmp_path / "bins"
    _exe(bins, "python3.12")
    mcp_env(
        MCP_STDIO_ALLOWED_DIRS=str(allowed),
        MCP_STDIO_TRUSTED_BIN_DIRS=str(bins),
        MCP_ALLOWED_COMMANDS="python3.12",
    )
    validate_command(["python3.12", str(allowed / "ok.py")])


def test_allowlist_entries_with_separators_are_ignored(
    allowed: Path, tmp_path: Path, mcp_env: Any
) -> None:
    bins = tmp_path / "bins"
    fake = _exe(bins, "tool")
    mcp_env(
        MCP_STDIO_ALLOWED_DIRS=str(allowed),
        MCP_STDIO_TRUSTED_BIN_DIRS=str(bins),
        MCP_ALLOWED_COMMANDS=f"{fake},../tool,python",
    )
    with pytest.raises(ValueError):
        validate_command([str(fake)])
    with pytest.raises(ValueError):
        validate_command(["tool"])


SHELLS_AND_WRAPPERS = [
    "sh", "bash", "zsh", "dash", "ksh", "csh", "tcsh", "fish", "env", "xargs",
    "busybox", "ssh", "cmd", "powershell", "pwsh", "nohup", "timeout", "sudo",
    "nice", "stdbuf", "find", "awk", "perl",
]  # fmt: skip


@POSIX
@pytest.mark.parametrize("name", SHELLS_AND_WRAPPERS)
def test_shells_and_exec_wrappers_are_denied_even_when_allowlisted(
    name: str, allowed: Path, tmp_path: Path, mcp_env: Any
) -> None:
    bins = tmp_path / "bins"
    fake = _exe(bins, name)
    mcp_env(
        MCP_STDIO_ALLOWED_DIRS=str(allowed),
        MCP_STDIO_TRUSTED_BIN_DIRS=str(bins),
        MCP_ALLOWED_COMMANDS=f"python,{name}",
    )
    for argv in (
        [name, "-c", "id"],
        [str(fake), "FOO=1", sys.executable, str(allowed / "ok.py")],
        [name, "FOO=1", "python", str(allowed / "ok.py")],
    ):
        with pytest.raises(ValueError, match="(?i)shell|wrapper|denied|never"):
            validate_command(argv)


def test_env_wrapper_in_front_of_python_refused(allowed: Path) -> None:
    for argv in (
        ["env", "FOO=1", "python", str(allowed / "ok.py")],
        ["/usr/bin/env", "python", str(allowed / "ok.py")],
        ["sh", "-c", "python x.py"],
    ):
        with pytest.raises(ValueError):
            validate_command(argv)


async def test_spawn_never_uses_a_shell_and_pins_the_cwd(
    allowed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    async def fake_exec(*argv: str, **kwargs: Any) -> Any:
        seen["argv"], seen["kwargs"] = argv, kwargs
        return object()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.chdir(allowed.parent)
    # `;` and `$()` in an argument stay literal: exec, never a shell.
    await spawn([sys.executable, str(allowed / "ok.py"), "a;b", "$(id)", "`id`"])
    assert seen["argv"][-3:] == ("a;b", "$(id)", "`id`")
    assert "shell" not in seen["kwargs"]
    assert Path(seen["kwargs"]["cwd"]).resolve() == allowed.resolve()
    assert Path(seen["kwargs"]["cwd"]).resolve() != Path.cwd().resolve()


async def test_spawn_without_a_script_uses_a_private_empty_cwd(
    mcp_env: Any, allowed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp_env(MCP_STDIO_ALLOWED_DIRS=str(allowed), MCP_STDIO_ALLOWED_MODULES="my_server")
    seen: dict[str, Any] = {}

    async def fake_exec(*argv: str, **kwargs: Any) -> Any:
        seen["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    await spawn([sys.executable, "-m", "my_server"])
    cwd = Path(seen["kwargs"]["cwd"])
    assert cwd.is_dir() and not any(cwd.iterdir()) and cwd != Path.cwd()
    if os.name != "nt":
        assert stat.S_IMODE(cwd.stat().st_mode) & 0o077 == 0
