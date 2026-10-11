"""
AGENTS.md / AGENTS.yaml runtime contract loader.

Defines the machine-readable contract that gates agent capabilities at
runtime. Loader reads a YAML file describing
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
    """Schema and constraints on the agent's structured output.

    ``json_schema`` (inline) or ``schema_ref`` (path to a JSON/YAML schema
    file, resolved relative to the contract file by ``load_contract``) enable
    full JSON-Schema validation of the agent output in
    :meth:`ContractValidator.check_output` — ``required_fields`` alone only
    checks key presence.
    """

    format: str = "json"
    schema_ref: str | None = None
    json_schema: dict[str, Any] | None = None
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
    """Load and validate an agent contract from a YAML file.

    A relative ``output_contract.schema_ref`` is resolved against the
    contract file's directory and loaded into ``json_schema`` (JSON or YAML),
    so the validator gets a compiled schema without a second I/O seam.
    Fail-closed: a declared but unreadable/invalid schema is a load error.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Agent contract not found: {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Contract must be a YAML mapping, got {type(raw).__name__}")
    contract = AgentContract.model_validate(raw)

    ref = contract.output_contract.schema_ref
    if ref and contract.output_contract.json_schema is None:
        schema_path = Path(ref)
        if not schema_path.is_absolute():
            schema_path = p.parent / schema_path
        if not schema_path.exists():
            raise FileNotFoundError(f"Contract schema_ref not found: {schema_path}")
        schema_raw = yaml.safe_load(schema_path.read_text(encoding="utf-8"))
        if not isinstance(schema_raw, dict):
            raise ValueError(
                f"schema_ref must contain a JSON-Schema object: {schema_path}"
            )
        contract.output_contract.json_schema = schema_raw
    return contract


class ContractViolationError(RuntimeError):
    """Raised when an agent action would violate its contract."""


class ContractValidator:
    """Validates runtime actions against a loaded ``AgentContract``."""

    def __init__(self, contract: AgentContract) -> None:
        self._contract = contract
        self._allowed_tools = set(contract.capabilities.allowed_tools)
        self._must_not = set(contract.capabilities.must_not)
        # Compile the output schema once (fail-closed on an invalid schema)
        # instead of re-parsing it on every check_output call.
        self._schema_validator = None
        schema = contract.output_contract.json_schema
        if schema:
            from jsonschema import Draft7Validator

            Draft7Validator.check_schema(schema)
            self._schema_validator = Draft7Validator(schema)

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
        """Validate the agent output against the contract.

        Checks ``required_fields`` presence, then — when the contract carries
        a JSON Schema (inline ``json_schema`` or resolved ``schema_ref``) —
        full schema validation, so type/enum/nesting violations are caught,
        not just missing keys.
        """
        required = set(self._contract.output_contract.required_fields)
        missing = required - set(output.keys())
        if missing:
            raise ContractViolationError(
                f"Output missing required fields: {sorted(missing)}"
            )
        if self._schema_validator is not None:
            from jsonschema import ValidationError

            try:
                self._schema_validator.validate(output)
            except ValidationError as exc:
                raise ContractViolationError(
                    f"Output violates contract schema: {exc.message}"
                ) from exc
