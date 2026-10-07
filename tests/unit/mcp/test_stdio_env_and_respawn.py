"""Stdio escape corpus 3/3: spec env, argument hygiene, validation at spawn time."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

from core.mcp.client import MCPClient
from core.mcp.stdio_client_transport import (
    build_child_env,
    spawn,
    validate_command,
)

from .stdio_support import FAKE, POSIX

# ----------------------------------------------------------- (4) spec env


@pytest.mark.parametrize(
    "key",
    [
        "PATH", "path", "LD_PRELOAD", "LD_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES",
        "DYLD_LIBRARY_PATH", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONHOME",
        "PYTHONINSPECT", "NODE_OPTIONS", "NODE_PATH", "NODE_EXTRA_CA_CERTS",
        "BUN_INSTALL", "BUN_CONFIG_REGISTRY", "DENO_DIR", "DENO_AUTH_TOKENS",
        "UV_INDEX_URL", "UV_PYTHON", "PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL",
        "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
        "HTTP_PROXY", "https_proxy", "ALL_PROXY", "GIT_SSH_COMMAND", "GIT_ASKPASS",
        "BASH_ENV", "ENV", "IFS", "HOME", "NPM_CONFIG_REGISTRY", "npm_config_prefix",
        "PERL5OPT", "RUBYOPT", "JAVA_TOOL_OPTIONS",
    ],
)  # fmt: skip
def test_code_injection_env_keys_are_an_error_not_a_silent_drop(key: str) -> None:
    with pytest.raises(ValueError, match="MCP_STDIO_ALLOWED_ENV_KEYS"):
        build_child_env({key: "x"})


def test_denied_env_key_can_be_allowlisted_per_key(mcp_env: Any) -> None:
    mcp_env(MCP_STDIO_ALLOWED_ENV_KEYS="HTTPS_PROXY,node_extra_ca_certs")
    env = build_child_env({"HTTPS_PROXY": "http://p", "NODE_EXTRA_CA_CERTS": "/ca"})
    assert env["HTTPS_PROXY"] == "http://p"
    with pytest.raises(ValueError):
        build_child_env({"LD_PRELOAD": "/x.so"})  # not listed: still refused


def test_harmless_env_keys_and_python_output_knobs_pass() -> None:
    env = build_child_env({"MY_API_MODE": "1", "PYTHONUTF8": "1", "FAKE_MODE": "x"})
    assert env["MY_API_MODE"] == "1" and env["PYTHONUTF8"] == "1"


def test_env_values_are_hygienic() -> None:
    for bad in ("a\x00b", "a\nb"):
        with pytest.raises(ValueError):
            build_child_env({"OK_KEY": bad})
    for bad_key in ("A=B", "", "A B", "A\x00"):
        with pytest.raises(ValueError):
            build_child_env({bad_key: "v"})


@POSIX
def test_child_path_is_the_trusted_dirs_not_the_parents(
    monkeypatch: pytest.MonkeyPatch, mcp_env: Any, tmp_path: Path
) -> None:
    bins = tmp_path / "bins"
    bins.mkdir()
    mcp_env(MCP_STDIO_TRUSTED_BIN_DIRS=str(bins))
    monkeypatch.setenv("PATH", f".:{tmp_path}/evil:/usr/bin")
    path = build_child_env(None)["PATH"].split(os.pathsep)
    assert "." not in path and f"{tmp_path}/evil" not in path
    assert str(bins.resolve()) in path


# --------------------------------------------------------- (5) args hygiene


def test_argument_hygiene(allowed: Path) -> None:
    script = str(allowed / "ok.py")
    for bad in ("a\x00b", "a\nb", "a\rb", "x" * 5000, "@/etc/passwd", "@args.txt"):
        with pytest.raises(ValueError):
            validate_command([sys.executable, script, bad])
    with pytest.raises(ValueError, match="(?i)too many"):
        validate_command([sys.executable, script, *["a"] * 80])
    validate_command([sys.executable, script, "--port", "8080", "name@example.com"])


# ------------------------------------------------ (7) validated at spawn time


@POSIX
async def test_respawn_revalidates_what_changed_after_config_load(
    tmp_path: Path, mcp_env: Any
) -> None:
    root = tmp_path / "srv"
    root.mkdir(mode=0o755)
    script = root / "server.py"
    shutil.copy(FAKE, script)
    mcp_env(MCP_STDIO_ALLOWED_DIRS=str(root))
    client = MCPClient(command=[sys.executable, str(script)])
    await client.connect()
    await client.disconnect()
    script.chmod(0o666)  # the file turns world-writable between respawns
    with pytest.raises(ValueError, match="(?i)world-writable"):
        await client.connect()
    script.chmod(0o644)
    outside = tmp_path / "evil.py"
    outside.write_text("pass\n")
    script.unlink()
    script.symlink_to(outside)  # ... or is swapped for a symlink out of the dir
    with pytest.raises(ValueError, match="(?i)outside|allowed"):
        await client.connect()


async def test_spawn_itself_validates_without_a_prior_resolve(allowed: Path) -> None:
    with pytest.raises(ValueError):
        await spawn([sys.executable, "-c", "print(1)"])
    with pytest.raises(ValueError):
        await spawn(["sh", "-c", "id"])
