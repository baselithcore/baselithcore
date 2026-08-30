"""Tests for per-subagent scope on VirtualAgentSpec."""

from __future__ import annotations

import pytest

from core.orchestration.handlers.swarm_agents import (
    VirtualAgentSpec,
    contract_for_spec,
    detect_scope_conflicts,
)

pytestmark = [pytest.mark.unit]


def _spec(name: str, **kwargs) -> VirtualAgentSpec:
    return VirtualAgentSpec(
        name=name,
        role="worker",
        capabilities=["analysis"],
        system_prompt="do work",
        **kwargs,
    )


class TestSpecFields:
    def test_scope_fields_default_open(self):
        spec = _spec("a")
        assert spec.allowed_tools is None
        assert spec.path_scope is None
        assert spec.model is None


class TestContractForSpec:
    def test_none_without_allowed_tools(self):
        assert contract_for_spec(_spec("a")) is None

    def test_validator_enforces_allowed_tools(self):
        from core.orchestration.contract import ContractViolationError

        validator = contract_for_spec(_spec("a", allowed_tools=["search", "summarize"]))
        assert validator is not None
        validator.check_tool_call("search")
        with pytest.raises(ContractViolationError):
            validator.check_tool_call("delete_db")


class TestScopeConflicts:
    def test_disjoint_scopes_no_conflict(self):
        specs = [
            _spec("a", path_scope=["src/api/**"]),
            _spec("b", path_scope=["src/ui/**"]),
        ]
        assert detect_scope_conflicts(specs) == []

    def test_overlapping_scopes_reported(self):
        specs = [
            _spec("a", path_scope=["src/**"]),
            _spec("b", path_scope=["src/api/**"]),
        ]
        conflicts = detect_scope_conflicts(specs)
        assert len(conflicts) == 1
        assert "a" in conflicts[0] and "b" in conflicts[0]

    def test_identical_patterns_conflict(self):
        specs = [
            _spec("a", path_scope=["docs/*.md"]),
            _spec("b", path_scope=["docs/*.md"]),
        ]
        assert len(detect_scope_conflicts(specs)) == 1

    def test_unscoped_specs_ignored(self):
        specs = [_spec("a"), _spec("b", path_scope=["src/**"])]
        assert detect_scope_conflicts(specs) == []
