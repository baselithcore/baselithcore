"""Produce an eval recording by running the real agent against a provider script.

The regression gate grades a :class:`~core.evaluation.regression_runner.RecordedRun`
against a :class:`~core.evaluation.trajectory.TrajectoryCase`. Where that run
comes from decides what the gate can catch, and until now it came from a
hand-written JSON file: ``evals/runs/recorded_runs.json`` is thirty objects
somebody typed, graded against thirty expectations somebody typed. It *cannot*
go red for a change to the agent, the prompt assembly, the router, the tool
schemas or the output parsing — the only things it can report are an edited
YAML file or a bug in the evaluator itself.

This module closes that gap without adding an API key to CI. A scenario carries
the provider's side of a conversation — the same cassette the golden tests
replay — plus the tools the agent was given. Replaying it runs the **real**
loop: the real prompt assembly, the real tool dispatch with its gates and
untrusted-output envelope, the real message history, the real answer parsing.
The trajectory that comes out is then graded by the existing evaluator.

So a prompt that stops mentioning a tool, a dispatcher that drops an argument,
a loop that stops feeding results back — each moves the produced trajectory, or
trips a :class:`~core.evaluation.cassette.CassetteMismatch`, and the gate fails.
The cost is that a scenario's provider turns are fixed: this measures the
*runtime*, not the model. Model quality is the nightly judge's job.

Tools are stubs on purpose. A scenario declares what each tool returns, so the
run is deterministic and hermetic; what is under test is the loop around the
tool, not the tool.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from core.evaluation.cassette import Cassette, RecordedLLMService, Turn
from core.evaluation.regression_runner import RecordedRun
from core.observability.logging import get_logger

logger = get_logger(__name__)

__all__ = [
    "ReplayError",
    "ReplayScenario",
    "ReplayTool",
    "load_scenarios",
    "replay_scenario",
]


class ReplayError(RuntimeError):
    """A scenario could not be loaded or replayed."""


@dataclass(frozen=True)
class ReplayTool:
    """A deterministic stand-in for one tool the agent may call.

    Attributes:
        name: Tool name, as the model will call it.
        description: What the model is told the tool does. Part of the prompt,
            so changing it can legitimately change a trajectory.
        category: Autonomy category; drives the approval gate and the ledger.
            Defaults to ``read_only`` so a scenario does not silently exercise
            the effectful path.
        returns: What the stub hands back. Any JSON value.
        fails: When set, the stub raises with this message instead, so a
            scenario can pin how the loop reports a failing tool.
    """

    name: str
    description: str = ""
    category: str = "read_only"
    returns: Any = ""
    fails: str | None = None


@dataclass(frozen=True)
class ReplayScenario:
    """One case's worth of agent execution, ready to run.

    Attributes:
        case_id: Must match a case in the eval corpus; that is how the produced
            run finds its expectations.
        prompt: The user turn the agent is given.
        cassette: The provider's side of the conversation.
        tools: The tools the agent is constructed with.
        system_prompt: Optional system prompt for the agent.
        cost_usd: Cost attributed to the run. A replay spends nothing, so this
            is the recorded cost of the original capture, carried so the case's
            budget assertion still means something.
    """

    case_id: str
    prompt: str
    cassette: Cassette
    tools: list[ReplayTool] = field(default_factory=list)
    system_prompt: str | None = None
    cost_usd: float = 0.0


def _tool_definitions(
    scenario: ReplayScenario, recorder: list[dict[str, Any]]
) -> list[Any]:
    """Build the agent's tools, each recording its own invocation.

    The recorder is what becomes the trajectory: the arguments are whatever the
    loop actually dispatched, so an argument the dispatcher drops or renames
    shows up as a failed assertion rather than passing unnoticed.
    """
    from core.reasoning.react import ToolDefinition

    definitions = []
    for tool in scenario.tools:

        def _make(spec: ReplayTool) -> Any:
            def _call(**kwargs: Any) -> Any:
                started = time.perf_counter()
                entry: dict[str, Any] = {
                    "name": spec.name,
                    "args": dict(kwargs),
                    "ok": spec.fails is None,
                }
                recorder.append(entry)
                if spec.fails is not None:
                    entry["latency_ms"] = 0
                    raise RuntimeError(spec.fails)
                entry["latency_ms"] = int((time.perf_counter() - started) * 1000)
                return spec.returns

            _call.__name__ = spec.name
            _call.__doc__ = spec.description or spec.name
            return _call

        definitions.append(
            ToolDefinition(
                name=tool.name,
                fn=_make(tool),
                description=tool.description or tool.name,
                category=tool.category,
            )
        )
    return definitions


async def replay_scenario(scenario: ReplayScenario) -> RecordedRun:
    """Run the agent against the scenario's script and record what it did.

    Args:
        scenario: The scenario to replay.

    Returns:
        The run, in the shape the regression gate grades.

    Raises:
        ReplayError: The loop diverged from the script, or failed outright.
            Both are gate failures: a cassette mismatch means the conversation
            the loop builds has changed, which is exactly what this is for.
    """
    from core.agent import Agent
    from core.evaluation.cassette import CassetteMismatch

    trajectory: list[dict[str, Any]] = []
    agent: Agent[str] = Agent(
        tools=_tool_definitions(scenario, trajectory),
        system_prompt=scenario.system_prompt,
        llm_service=RecordedLLMService(scenario.cassette),
        max_iterations=max(len(scenario.cassette.turns) + 1, 2),
    )

    started = time.perf_counter()
    try:
        result = await agent.run(scenario.prompt)
    except CassetteMismatch as exc:
        raise ReplayError(f"{scenario.case_id}: {exc}") from exc
    except Exception as exc:
        raise ReplayError(f"{scenario.case_id}: agent run failed: {exc}") from exc
    elapsed_ms = int((time.perf_counter() - started) * 1000)

    return RecordedRun(
        case_id=scenario.case_id,
        output_text=result.text,
        # The evaluator reads ``name``/``args``/``ok``; the latency of a stub
        # is meaningless, so the case's budget is checked against the run's
        # own wall clock below rather than against these.
        trajectory=[dict(entry) for entry in trajectory],  # type: ignore[misc]
        latency_ms=elapsed_ms,
        cost_usd=scenario.cost_usd,
    )


def _scenario_from(raw: dict[str, Any], path: Path) -> ReplayScenario:
    """Build one scenario from its mapping, or say precisely what is missing."""
    case_id = raw.get("case_id")
    if not isinstance(case_id, str) or not case_id:
        raise ReplayError(f"{path}: every scenario needs a non-empty 'case_id'")
    prompt = raw.get("prompt")
    if not isinstance(prompt, str) or not prompt:
        raise ReplayError(f"{case_id} ({path}): 'prompt' is required")
    turns = raw.get("turns")
    if not isinstance(turns, list) or not turns:
        raise ReplayError(f"{case_id} ({path}): 'turns' must be a non-empty list")

    try:
        cassette = Cassette(
            name=case_id,
            description=str(raw.get("description", "")),
            turns=[Turn.from_dict(turn) for turn in turns],
        )
    except Exception as exc:
        raise ReplayError(f"{case_id} ({path}): malformed turns: {exc}") from exc

    tools = []
    for spec in raw.get("tools", []) or []:
        if not isinstance(spec, dict) or not spec.get("name"):
            raise ReplayError(f"{case_id} ({path}): every tool needs a 'name'")
        tools.append(
            ReplayTool(
                name=str(spec["name"]),
                description=str(spec.get("description", "")),
                category=str(spec.get("category", "read_only")),
                returns=spec.get("returns", ""),
                fails=spec.get("fails"),
            )
        )

    return ReplayScenario(
        case_id=case_id,
        prompt=prompt,
        cassette=cassette,
        tools=tools,
        system_prompt=raw.get("system_prompt"),
        cost_usd=float(raw.get("cost_usd", 0.0)),
    )


def load_scenarios(directory: Path | str) -> list[ReplayScenario]:
    """Load every ``.yaml`` / ``.yml`` scenario file under ``directory``.

    Args:
        directory: Scenario directory.

    Returns:
        Every scenario found, in filename order.

    Raises:
        ReplayError: The directory is missing, or a scenario is malformed. A
            corpus that cannot be loaded is a gate failure, never an empty run.
    """
    d = Path(directory)
    if not d.is_dir():
        raise ReplayError(f"scenario directory does not exist: {d}")

    scenarios: list[ReplayScenario] = []
    seen: set[str] = set()
    for path in sorted(d.glob("*.y*ml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        raw_list = data if isinstance(data, list) else [data]
        for raw in raw_list:
            if not isinstance(raw, dict):
                raise ReplayError(f"{path}: each scenario must be a mapping")
            scenario = _scenario_from(raw, path)
            if scenario.case_id in seen:
                raise ReplayError(f"{path}: duplicate scenario {scenario.case_id}")
            seen.add(scenario.case_id)
            scenarios.append(scenario)
    return scenarios
