"""The A2A internal-endpoint default must be environment-aware.

A2A meshes run peers on private networks, so dev permits internal hosts by
default; production denies them so a peer endpoint cannot be steered at cloud
metadata / Redis / Postgres (parity with MCP_ALLOW_INTERNAL_ENDPOINTS and the
webhook SSRF guard). An explicit A2A_ALLOW_INTERNAL_ENDPOINTS overrides both.
"""

from __future__ import annotations

import pytest

from core.a2a.client import _default_allow_internal_endpoints

_ENV = "A2A_ALLOW_INTERNAL_ENDPOINTS"


def test_denies_internal_by_default_in_production(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(_ENV, raising=False)
    monkeypatch.setattr("core.config.environment.is_production_env", lambda: True)
    assert _default_allow_internal_endpoints() is False


def test_allows_internal_by_default_in_development(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(_ENV, raising=False)
    monkeypatch.setattr("core.config.environment.is_production_env", lambda: False)
    assert _default_allow_internal_endpoints() is True


@pytest.mark.parametrize("value", ["true", "1", "yes", "on", "TRUE"])
def test_explicit_true_opts_in_even_in_production(
    value: str, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv(_ENV, value)
    monkeypatch.setattr("core.config.environment.is_production_env", lambda: True)
    assert _default_allow_internal_endpoints() is True


@pytest.mark.parametrize("value", ["false", "0", "no", "off", ""])
def test_explicit_false_locks_down_even_in_development(
    value: str, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv(_ENV, value)
    monkeypatch.setattr("core.config.environment.is_production_env", lambda: False)
    assert _default_allow_internal_endpoints() is False
