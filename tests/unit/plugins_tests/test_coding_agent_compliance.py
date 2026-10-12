"""coding-agent's regulatory wiring: AI inventory entry, provenance tags, MCP payloads."""

from __future__ import annotations

import hashlib

import pytest

from baselith.core.compliance.registry import (
    get_ai_system_registry,
    reset_ai_system_registry,
)
from baselith.plugins.coding_agent.compliance import (
    SYSTEM_ID,
    mark_generated,
    register_ai_system,
)
from baselith.plugins.coding_agent.tools import build_coding_tool_definitions
from baselith.plugins.coding_agent.types import CodingResult


@pytest.fixture(autouse=True)
def _fresh_registry(monkeypatch):
    monkeypatch.delenv("COMPLIANCE_REGISTRY_DB_PATH", raising=False)
    reset_ai_system_registry()
    yield
    reset_ai_system_registry()


async def test_registration_is_idempotent_and_refreshes_its_facts():
    assert await register_ai_system("1.1.0") == SYSTEM_ID
    assert await register_ai_system("1.1.0") == SYSTEM_ID
    stored = await get_ai_system_registry().get(SYSTEM_ID)
    assert stored is not None and stored.generates_synthetic_content
    assert stored.version == "1.1.0"
    await register_ai_system("1.2.0")
    assert (await get_ai_system_registry().get(SYSTEM_ID)).version == "1.2.0"


async def test_a_broken_registry_never_fails(monkeypatch):
    import baselith.core.compliance

    def boom():
        raise RuntimeError("store down")

    monkeypatch.setattr(baselith.core.compliance, "get_ai_system_registry", boom)
    assert await register_ai_system("1.1.0") is None


def test_generated_code_carries_a_tag_bound_to_its_bytes():
    code = "def f() -> int:\n    return 1\n"
    tag = mark_generated(code)
    assert tag is not None and tag["content_class"] == "ai_generated"
    assert tag["content_sha256"] == hashlib.sha256(code.encode()).hexdigest()
    assert mark_generated("") is None


async def test_mcp_tools_return_the_proof_with_the_code(monkeypatch):
    tools = {t["name"]: t for t in build_coding_tool_definitions()}
    assert all("language" in t["input_schema"]["properties"] for t in tools.values())
    assert tools["generate_tests"]["category"] == "destructive"
    assert tools["explain_code"]["category"] == "read_only"

    async def fake_fix(self, code, error, context="", *, language=None):
        return CodingResult(
            success=True,
            original_code=code,
            final_code="fixed",
            iterations=2,
            tests_passed=3,
            tests_total=3,
            test_code="tests",
            changes=["one"],
            loop_status="success",
            tests_reproduce_bug=True,
        )

    from baselith.plugins.coding_agent.agent import CodingAgent

    monkeypatch.setattr(CodingAgent, "fix_code", fake_fix)
    out = await tools["fix_code"]["handler"]("bad", "err", language="python")
    assert out["fixed_code"] == "fixed" and out["test_code"] == "tests"
    assert (out["tests_passed"], out["tests_total"], out["loop_status"]) == (
        3,
        3,
        "success",
    )
    assert out["tests_reproduce_bug"] is True and out["changes"] == ["one"]
