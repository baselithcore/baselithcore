"""MCP unit-test defaults.

The production default is fail-closed: ``MCP_AUTONOMY_LEVEL=supervised`` with
tool category defaulting to ``destructive``. The protocol tests in this
package exercise transport/dispatch behavior with inert fixture tools, so they
run fully autonomous; tests that target the autonomy gate itself pass explicit
policies (or manage the env var directly) and are unaffected.
"""

import pytest

import core.config.mcp as mcp_config_module


@pytest.fixture(autouse=True)
def _mcp_full_autonomy(monkeypatch):
    monkeypatch.setenv("MCP_AUTONOMY_LEVEL", "fully_autonomous")
    monkeypatch.setattr(mcp_config_module, "_mcp_config", None)
    yield
    monkeypatch.setattr(mcp_config_module, "_mcp_config", None)
