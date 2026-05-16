"""
AGENTS.md / AGENTS.yaml runtime contract loader.

Defines the machine-readable contract that gates agent capabilities at
runtime (book ch10 spec-first). Loader reads a YAML file describing
Identity, Capabilities (MAY / MUST NOT), Output Contract, Quality Gates.

Integration hook: ``Orchestrator`` should load a contract at startup and
``ContractValidator`` should reject tool calls / outputs that violate it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator


class Capabilities(BaseModel):
    """Allowed and forbidden capabilities the agent may exercise."""

    may: list[str] = Field(default_factory=list)
    must_not: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)


class OutputContract(BaseModel):
    """Schema and constraints on the agent's structured output."""

    format: str = "json"
    schema_ref: str | None = None
    max_tokens: int | None = None
    required_fields: list[str] = Field(default_factory=list)


class QualityGates(BaseModel):
    """Gates the agent run must satisfy before being considered successful."""

    min_eval_pass_rate: float = Field(default=0.90, ge=0.0, le=1.0)
    max_latency_ms: int = Field(default=15000, gt=0)
    max_cost_usd: float = Field(default=0.50, ge=0.0)
    min_test_coverage: float = Field(default=0.85, ge=0.0, le=1.0)


class AgentContract(BaseModel):
    """Full machine-readable agent contract."""

    name: str
    version: str
    identity: str
    capabilities: Capabilities = Field(default_factory=Capabilities)
    output_contract: OutputContract = Field(default_factory=OutputContract)
    quality_gates: QualityGates = Field(default_factory=QualityGates)

    @field_validator("version")
    @classmethod
    def _semver_shape(cls, v: str) -> str:
        parts = v.split(".")
        if len(parts) < 2 or not all(p.isdigit() for p in parts[:2]):
            raise ValueError("version must be semver-like (e.g. '1.2.0')")
        return v


def load_contract(path: str | Path) -> AgentContract:
    """Load and validate an agent contract from a YAML file."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Agent contract not found: {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Contract must be a YAML mapping, got {type(raw).__name__}")
    return AgentContract.model_validate(raw)


class ContractViolationError(RuntimeError):
    """Raised when an agent action would violate its contract."""


class ContractValidator:
    """Validates runtime actions against a loaded ``AgentContract``."""

    def __init__(self, contract: AgentContract) -> None:
        self._contract = contract
        self._allowed_tools = set(contract.capabilities.allowed_tools)
        self._must_not = set(contract.capabilities.must_not)

    def check_tool_call(self, tool_name: str) -> None:
        """Raise ``ContractViolationError`` if ``tool_name`` is not permitted."""
        if self._allowed_tools and tool_name not in self._allowed_tools:
            raise ContractViolationError(
                f"Tool '{tool_name}' not in allowed_tools "
                f"(contract={self._contract.name}@{self._contract.version})"
            )
        if tool_name in self._must_not:
            raise ContractViolationError(
                f"Tool '{tool_name}' is explicitly forbidden by contract"
            )

    def check_output(self, output: dict[str, Any]) -> None:
        """Raise ``ContractViolationError`` if required output fields are missing."""
        required = set(self._contract.output_contract.required_fields)
        missing = required - set(output.keys())
        if missing:
            raise ContractViolationError(
                f"Output missing required fields: {sorted(missing)}"
            )
