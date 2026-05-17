"""Unit tests for ``core.orchestration.contract``."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.orchestration.contract import (
    AgentContract,
    Capabilities,
    ContractValidator,
    ContractViolationError,
    OutputContract,
    QualityGates,
    load_contract,
)


def _make_contract(
    allowed: list[str] | None = None,
    must_not: list[str] | None = None,
    required_fields: list[str] | None = None,
) -> AgentContract:
    return AgentContract(
        name="atlas",
        version="1.0.0",
        identity="research assistant",
        capabilities=Capabilities(
            allowed_tools=allowed or [],
            must_not=must_not or [],
        ),
        output_contract=OutputContract(required_fields=required_fields or []),
    )


class TestAgentContract:
    def test_minimal_contract_valid(self) -> None:
        c = AgentContract(name="x", version="0.1", identity="i")
        assert c.quality_gates.min_eval_pass_rate == 0.90
        assert c.quality_gates.max_cost_usd == 0.50

    def test_version_rejects_non_semver(self) -> None:
        with pytest.raises(ValueError):
            AgentContract(name="x", version="vNEXT", identity="i")

    def test_quality_gate_bounds(self) -> None:
        with pytest.raises(ValueError):
            QualityGates(min_eval_pass_rate=1.5)


class TestContractValidator:
    def test_allowed_tool_passes(self) -> None:
        v = ContractValidator(_make_contract(allowed=["search", "read"]))
        v.check_tool_call("search")

    def test_unknown_tool_blocked(self) -> None:
        v = ContractValidator(_make_contract(allowed=["search"]))
        with pytest.raises(ContractViolationError):
            v.check_tool_call("write")

    def test_must_not_takes_precedence(self) -> None:
        v = ContractValidator(_make_contract(allowed=["rm_rf"], must_not=["rm_rf"]))
        with pytest.raises(ContractViolationError):
            v.check_tool_call("rm_rf")

    def test_empty_allow_list_means_no_restriction(self) -> None:
        v = ContractValidator(_make_contract(allowed=[], must_not=["rm_rf"]))
        v.check_tool_call("anything")
        with pytest.raises(ContractViolationError):
            v.check_tool_call("rm_rf")

    def test_output_required_fields(self) -> None:
        v = ContractValidator(_make_contract(required_fields=["answer", "sources"]))
        with pytest.raises(ContractViolationError) as exc:
            v.check_output({"answer": "x"})
        assert "sources" in str(exc.value)
        v.check_output({"answer": "x", "sources": []})


class TestLoadContract:
    def test_load_from_yaml(self, tmp_path: Path) -> None:
        p = tmp_path / "agent.yaml"
        p.write_text(
            """
name: atlas
version: 1.0.0
identity: research assistant
capabilities:
  allowed_tools: [search, read]
  must_not: [rm_rf]
output_contract:
  format: json
  required_fields: [answer, sources]
quality_gates:
  min_eval_pass_rate: 0.92
""".strip()
        )
        c = load_contract(p)
        assert c.name == "atlas"
        assert c.capabilities.allowed_tools == ["search", "read"]
        assert c.quality_gates.min_eval_pass_rate == 0.92

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_contract(tmp_path / "nope.yaml")

    def test_non_mapping_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.yaml"
        p.write_text("- just\n- a\n- list\n")
        with pytest.raises(ValueError):
            load_contract(p)
