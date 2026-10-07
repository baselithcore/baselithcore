"""MCP unit-test defaults.

The production default is fail-closed: ``MCP_AUTONOMY_LEVEL=supervised`` with
tool category defaulting to ``destructive``. The protocol tests in this
package exercise transport/dispatch behavior with inert fixture tools, so they
run fully autonomous; tests that target the autonomy gate itself pass explicit
policies (or manage the env var directly) and are unaffected.
"""

import sys
from pathlib import Path
from typing import Any

import pytest

import core.config.mcp as mcp_config_module

from .stdio_support import exe

#: The one directory stdio scripts may run from in these tests. Stdio spawning
#: is deny-by-default (``MCP_STDIO_ALLOWED_DIRS`` empty), so the fake servers'
#: directory is approved here exactly as an operator would approve theirs.
FAKES_DIR = Path(__file__).parent / "fakes"


@pytest.fixture(autouse=True)
def _mcp_full_autonomy(monkeypatch):
    monkeypatch.setenv("MCP_AUTONOMY_LEVEL", "fully_autonomous")
    monkeypatch.setenv("MCP_STDIO_ALLOWED_DIRS", str(FAKES_DIR))
    monkeypatch.setattr(mcp_config_module, "_mcp_config", None)
    yield
    monkeypatch.setattr(mcp_config_module, "_mcp_config", None)


@pytest.fixture
def internal_ok(monkeypatch):
    """Let in-process fake servers (``https://mcp.example.com``) pass the SSRF guard.

    The guard resolves DNS and fails closed; a MockTransport never touches the
    network, so the allow-internal switch is the honest way to exercise the
    rest of the client. Tests of the guard itself do NOT use this fixture.
    """
    monkeypatch.setenv("MCP_ALLOW_INTERNAL_ENDPOINTS", "true")
    monkeypatch.setattr(mcp_config_module, "_mcp_config", None)
    yield


@pytest.fixture
def mcp_env(monkeypatch):
    """Set MCP_* env knobs and rebuild the config singleton."""

    def _set(**values: str) -> None:
        for key, value in values.items():
            monkeypatch.setenv(key, value)
        monkeypatch.setattr(mcp_config_module, "_mcp_config", None)

    return _set


@pytest.fixture
def allowed(tmp_path: Path, mcp_env: Any) -> Path:
    """An admin-approved stdio script directory holding one harmless script per runtime."""
    root = tmp_path / "servers"
    root.mkdir(mode=0o755)
    root.chmod(0o755)
    (root / "ok.py").write_text("print('hi')\n")
    (root / "ok.js").write_text("console.log('hi')\n")
    (root / "ok.ts").write_text("console.log('hi')\n")
    mcp_env(MCP_STDIO_ALLOWED_DIRS=str(root))
    return root


@pytest.fixture
def bindir(tmp_path: Path, mcp_env: Any, allowed: Path) -> Path:
    """A trusted bin directory with fake interpreters and runners."""
    bins = tmp_path / "trusted-bin"
    for name in ("node", "deno", "bun", "uv", "npx", "uvx", "bunx", "python3"):
        exe(bins, name)
    bins.chmod(0o755)
    mcp_env(
        MCP_STDIO_ALLOWED_DIRS=str(allowed),
        MCP_STDIO_TRUSTED_BIN_DIRS=f"{bins},{Path(sys.executable).parent}",
    )
    return bins
