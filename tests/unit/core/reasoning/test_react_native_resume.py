"""The native structured ReAct loop resumes without repeating effects.

``run_native_loop`` executes through the same checkpoint/ledger wrapper as the
text loop; these tests pin that a resumed native run whose regenerated turns
ask for the same effects in a different order replays them.
"""

from __future__ import annotations

from core.orchestration.autonomy import AutonomyLevel, AutonomyPolicy
from core.orchestration.checkpoint import Checkpoint, CheckpointManager
from core.orchestration.checkpoint_memory import InMemoryCheckpointStore
from core.orchestration.idempotency import InMemoryToolLedger
from core.reasoning.react import ReActAgent, ToolDefinition
from core.services.llm.tool_calling import LLMResult, ToolCall
from tests.unit.core.reasoning.test_react_native import ScriptedNativeLLM


def _turn(*calls: tuple[str, dict]) -> LLMResult:
    return LLMResult(
        text=None,
        tool_calls=[
            ToolCall(id=f"id-{i}-{name}", name=name, arguments=args)
            for i, (name, args) in enumerate(calls)
        ],
        stop_reason="tool_use",
    )


def _tools(ran: list[str]) -> list[ToolDefinition]:
    async def charge(amount: int) -> str:
        ran.append(f"charge:{amount}")
        return f"charged {amount}"

    async def notify(to: str) -> str:
        ran.append(f"notify:{to}")
        return f"notified {to}"

    return [
        ToolDefinition(name="charge", fn=charge, description="d", category="mutating"),
        ToolDefinition(name="notify", fn=notify, description="d", category="mutating"),
    ]


def _agent(ran: list[str], script: list[LLMResult], **kwargs) -> ReActAgent:
    return ReActAgent(
        tools=_tools(ran),
        llm_service=ScriptedNativeLLM(script),
        native_tools=True,
        autonomy_policy=AutonomyPolicy(level=AutonomyLevel.FULLY_AUTONOMOUS),
        **kwargs,
    )


_FIRST = [
    _turn(("charge", {"amount": 10})),
    _turn(("notify", {"to": "ops"})),
    LLMResult(text="done"),
]
_RESUMED = [
    _turn(("notify", {"to": "ops"}), ("charge", {"amount": 10})),
    LLMResult(text="done"),
]


async def test_native_loop_resumes_from_a_checkpoint_on_a_different_path() -> None:
    ran: list[str] = []
    store = InMemoryCheckpointStore()
    checkpoint = Checkpoint(run_id="native-cp", query="q")
    await store.save(checkpoint)

    first = _agent(ran, list(_FIRST), checkpoint=CheckpointManager(store, checkpoint))
    first._tool_ledger = InMemoryToolLedger()
    assert (await first.run("q")).final_answer == "done"

    loaded = await store.load("native-cp")
    assert loaded is not None
    resumed = _agent(ran, list(_RESUMED), checkpoint=CheckpointManager(store, loaded))
    # A fresh ledger: the checkpoint alone must be what replays.
    resumed._tool_ledger = InMemoryToolLedger()
    result = await resumed.run("q")

    assert result.final_answer == "done"
    assert ran == ["charge:10", "notify:ops"]


async def test_native_loop_resumes_from_the_ledger_on_a_different_path() -> None:
    ran: list[str] = []
    ledger = InMemoryToolLedger()

    for script in (_FIRST, _RESUMED):
        # A resumed run without a checkpoint: a new agent, the same run id.
        agent = _agent(ran, list(script))
        agent._tool_ledger = ledger
        agent._ledger_run_id = "native-ledger"
        await agent.run("q")

    assert ran == ["charge:10", "notify:ops"]


async def test_native_loop_executes_a_genuine_repeat() -> None:
    ran: list[str] = []
    agent = _agent(
        ran,
        [
            _turn(("notify", {"to": "ops"})),
            _turn(("notify", {"to": "ops"})),
            LLMResult(text="done"),
        ],
    )
    agent._tool_ledger = InMemoryToolLedger()
    agent._ledger_run_id = "native-repeat"
    await agent.run("q")

    assert ran == ["notify:ops", "notify:ops"]
