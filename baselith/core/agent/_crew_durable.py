"""Durable execution for crews: each agent turn of a crew is a checkpoint step.

A crew used to be all-or-nothing under a checkpoint: a crash in task 4 of 5
re-ran tasks 1-3 on resume, paying for every LLM call again and repeating any
tool effect the run's ledger could not deduplicate. Here every agent call a
crew makes — a worker running its task, and in ``hierarchical`` mode the
manager's brief, review and the bounded revision — is recorded through
:meth:`~baselith.core.orchestration.checkpoint.CheckpointManager.run_step`
under a child scope of the run (see
:mod:`baselith.core.orchestration.checkpoint_scope`). A resumed crew replays
each recorded call's output instead of calling the agent, and the first call
not recorded runs for real.

Inside a call, the agent gets its own child scope as ``checkpoint=``, so the
tool calls of a task interrupted half-way are replayed too, and a nested crew
started by one of its tools finds that scope as the ambient checkpoint.

Keys are deterministic: the crew scope is identified by the process, the task
descriptions and the inputs; a call by its role, task index and a hash of its
prompt. A prompt embeds the outputs of earlier tasks, which a resumed run
replays verbatim, so the same call lands on the same key.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

from baselith.core.observability.logging import get_logger
from baselith.core.orchestration.checkpoint_scope import (
    bind_checkpoint,
    resolve_checkpoint,
)

if TYPE_CHECKING:
    from baselith.core.agent.agent import AgentResult
    from baselith.core.agent.crew import Crew

logger = get_logger(__name__)

__all__ = ["crew_scope", "durable_agent_run"]


def crew_scope(crew: Crew, inputs: dict[str, Any], checkpoint: Any | None) -> Any:
    """The crew's child scope of ``checkpoint`` (or the ambient one), if any."""
    parent = resolve_checkpoint(checkpoint)
    if parent is None:
        return None
    identity = {
        "process": crew.process,
        "tasks": [task.name or task.description for task in crew.tasks],
        "inputs": inputs,
    }
    return parent.scope("crew", identity)


def _prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]


def _encode_output(output: Any) -> dict[str, Any]:
    dump = getattr(output, "model_dump", None)
    if callable(dump):
        return {"model": dump(mode="json")}
    return {"value": output}


def _decode_output(agent: Any, encoded: dict[str, Any]) -> Any:
    if "model" in encoded:
        output_type = getattr(agent, "output_type", None)
        if output_type is not None:
            return output_type.model_validate(encoded["model"])
        return encoded["model"]
    return encoded.get("value")


async def durable_agent_run(
    agent: Any,
    prompt: str,
    scope: Any | None,
    *,
    role: str,
    index: int,
) -> AgentResult[Any]:
    """``agent.run(prompt)``, recorded as one step of ``scope`` when there is one.

    Args:
        agent: The agent to run.
        prompt: The full prompt.
        scope: The crew's checkpoint scope, or ``None`` (plain call).
        role: What this call is (``"task"``, ``"brief"``, ``"review"``,
            ``"revision"``).
        index: The task index.

    Returns:
        The agent's result; on replay, rebuilt from the record (``messages``
        are not recorded and come back empty).
    """
    from baselith.core.agent.agent import AgentResult

    if scope is None:
        return await agent.run(prompt)  # type: ignore[no-any-return]
    args = {"role": role, "index": index, "prompt": _prompt_hash(prompt)}
    # Drawn on every pass, replayed or not, so the occurrence is stable.
    call_scope = scope.scope(f"crew_{role}", args)

    async def _execute() -> dict[str, Any]:
        with bind_checkpoint(call_scope):
            result = await agent.run(
                prompt, checkpoint=call_scope, run_id=call_scope.ledger_run_id
            )
        return {
            "output": _encode_output(result.output),
            "text": result.text,
            "tool_calls_made": list(result.tool_calls_made),
            "iterations": result.iterations,
        }

    record = await scope.run_step(f"crew_{role}", args, _execute, category="crew")
    return AgentResult(
        output=_decode_output(agent, record["output"]),
        text=str(record.get("text", "")),
        tool_calls_made=list(record.get("tool_calls_made") or []),
        iterations=int(record.get("iterations", 0)),
    )
