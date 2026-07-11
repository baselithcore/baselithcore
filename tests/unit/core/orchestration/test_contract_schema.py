"""OutputContract JSON-Schema validation + swarm decomposition caps."""

import pytest

from core.orchestration.contract import (
    AgentContract,
    ContractValidator,
    ContractViolationError,
    load_contract,
)

SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["answer"],
    "additionalProperties": True,
}


def _contract(json_schema=None, required_fields=None):
    return AgentContract.model_validate(
        {
            "name": "t",
            "version": "1.0.0",
            "identity": "test agent",
            "output_contract": {
                "json_schema": json_schema,
                "required_fields": required_fields or [],
            },
        }
    )


def test_schema_pass():
    validator = ContractValidator(_contract(json_schema=SCHEMA))
    validator.check_output({"answer": "ok", "confidence": 0.9})


def test_schema_type_violation_caught():
    validator = ContractValidator(_contract(json_schema=SCHEMA))
    with pytest.raises(ContractViolationError, match="violates contract schema"):
        validator.check_output({"answer": 42})


def test_schema_range_violation_caught():
    validator = ContractValidator(_contract(json_schema=SCHEMA))
    with pytest.raises(ContractViolationError):
        validator.check_output({"answer": "ok", "confidence": 3.0})


def test_required_fields_still_checked_first():
    validator = ContractValidator(
        _contract(json_schema=SCHEMA, required_fields=["answer", "sources"])
    )
    with pytest.raises(ContractViolationError, match="missing required fields"):
        validator.check_output({"answer": "ok"})


def test_no_schema_keeps_legacy_behavior():
    validator = ContractValidator(_contract())
    validator.check_output({"anything": "goes"})


def test_invalid_schema_fails_closed_at_construction():
    with pytest.raises(Exception):
        ContractValidator(_contract(json_schema={"type": "not-a-real-type"}))


def test_load_contract_resolves_schema_ref(tmp_path):
    (tmp_path / "schema.json").write_text(
        '{"type": "object", "required": ["answer"]}', encoding="utf-8"
    )
    (tmp_path / "contract.yaml").write_text(
        "name: t\nversion: '1.0.0'\nidentity: test\n"
        "output_contract:\n  schema_ref: schema.json\n",
        encoding="utf-8",
    )
    contract = load_contract(tmp_path / "contract.yaml")
    assert contract.output_contract.json_schema == {
        "type": "object",
        "required": ["answer"],
    }
    with pytest.raises(ContractViolationError):
        ContractValidator(contract).check_output({})


def test_load_contract_missing_schema_ref_fails_closed(tmp_path):
    (tmp_path / "contract.yaml").write_text(
        "name: t\nversion: '1.0.0'\nidentity: test\n"
        "output_contract:\n  schema_ref: nope.json\n",
        encoding="utf-8",
    )
    with pytest.raises(FileNotFoundError):
        load_contract(tmp_path / "contract.yaml")


# ---------------------------------------------------------------------------
# Swarm decomposition cap
# ---------------------------------------------------------------------------


def test_max_dynamic_subtasks_default_and_env(monkeypatch):
    from core.orchestration.handlers.swarm_agents import max_dynamic_subtasks

    assert max_dynamic_subtasks() == 4
    monkeypatch.setenv("BASELITH_SWARM_MAX_SUBTASKS", "2")
    assert max_dynamic_subtasks() == 2
    monkeypatch.setenv("BASELITH_SWARM_MAX_SUBTASKS", "0")
    assert max_dynamic_subtasks() == 1  # floor: at least one task
    monkeypatch.setenv("BASELITH_SWARM_MAX_SUBTASKS", "junk")
    assert max_dynamic_subtasks() == 4


async def test_decomposition_truncates_model_output(monkeypatch):
    import json

    from core.orchestration.handlers.swarm_handler import SwarmHandler

    handler = SwarmHandler()

    class FloodLLM:
        async def generate_response(self, prompt, json=False):
            tasks = [
                {
                    "description": f"task {i}",
                    "capability": "analysis",
                    "agent_name": f"Agent{i}",
                    "agent_role": f"role{i}",
                    "agent_prompt": "do it",
                }
                for i in range(50)  # adversarial flood
            ]
            import json as json_lib

            return json_lib.dumps(tasks)

    handler._llm_service = FloodLLM()
    monkeypatch.setenv("BASELITH_SWARM_MAX_SUBTASKS", "3")
    tasks = await handler._decompose_task("big query", {})
    assert len(tasks) == 3
    assert json.dumps(tasks)  # still serializable dicts


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
