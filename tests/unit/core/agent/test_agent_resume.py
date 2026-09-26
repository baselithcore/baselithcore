"""The typed ``Agent`` resumes without repeating effects, whatever path it takes.

Every test counts real invocations of the tools, then re-runs the same run —
with the model asking for the same effects in a different order, the normal
shape of a regenerated turn — and asserts the counts did not move.
"""

from __future__ import annotations

from core.agent import Agent
from core.orchestration.checkpoint import Checkpoint, CheckpointManager
from core.orchestration.checkpoint_memory import InMemoryCheckpointStore
from core.orchestration.idempotency import InMemoryToolLedger
from core.reasoning.react import ToolDefinition
from core.services.llm.tool_calling import LLMResult, ToolCall
from tests.unit.core.agent.test_agent import _mock_service


class _Effects:
    """Two effectful tools that record every real execution."""

    def __init__(self) -> None:
        self.ran: list[str] = []

    def tools(self) -> list[ToolDefinition]:
        def charge(amount: int) -> str:
            self.ran.append(f"charge:{amount}")
            return f"charged {amount}"

        def notify(to: str) -> str:
            self.ran.append(f"notify:{to}")
            return f"notified {to}"

        return [
            ToolDefinition(
                name="charge", fn=charge, description="d", category="mutating"
            ),
            ToolDefinition(
                name="notify", fn=notify, description="d", category="mutating"
            ),
        ]


def _turn(*calls: tuple[str, dict]) -> LLMResult:
    return LLMResult(
        text="",
        tool_calls=[
            ToolCall(id=f"id-{i}-{name}", name=name, arguments=args)
            for i, (name, args) in enumerate(calls)
        ],
    )


def _first_pass():
    """charge, then notify, in two turns."""
    return _mock_service(
        [
            _turn(("charge", {"amount": 10})),
            _turn(("notify", {"to": "ops"})),
            LLMResult(text="done"),
        ]
    )


def _resumed_pass():
    """The regenerated run: notify first, both in one turn, then charge again."""
    return _mock_service(
        [
            _turn(("notify", {"to": "ops"}), ("charge", {"amount": 10})),
            LLMResult(text="done"),
        ]
    )


class TestLedgerResume:
    async def test_a_resumed_run_on_a_different_path_repeats_nothing(self) -> None:
        effects = _Effects()
        ledger = InMemoryToolLedger()

        await Agent(
            tools=effects.tools(), llm_service=_first_pass(), tool_ledger=ledger
        ).run("q", run_id="run-1")
        resumed = await Agent(
            tools=effects.tools(), llm_service=_resumed_pass(), tool_ledger=ledger
        ).run("q", run_id="run-1")

        assert effects.ran == ["charge:10", "notify:ops"]
        assert resumed.tool_calls_made == ["notify", "charge"]

    async def test_a_genuine_second_identical_call_executes(self) -> None:
        effects = _Effects()
        svc = _mock_service(
            [
                _turn(("notify", {"to": "ops"})),
                _turn(("notify", {"to": "ops"})),
                LLMResult(text="done"),
            ]
        )
        await Agent(
            tools=effects.tools(), llm_service=svc, tool_ledger=InMemoryToolLedger()
        ).run("q", run_id="run-2")

        assert effects.ran == ["notify:ops", "notify:ops"]

    async def test_two_identical_calls_in_one_turn_are_two_effects(self) -> None:
        effects = _Effects()
        svc = _mock_service(
            [
                _turn(("notify", {"to": "ops"}), ("notify", {"to": "ops"})),
                LLMResult(text="done"),
            ]
        )
        ledger = InMemoryToolLedger()
        await Agent(tools=effects.tools(), llm_service=svc, tool_ledger=ledger).run(
            "q", run_id="run-3"
        )
        assert effects.ran == ["notify:ops", "notify:ops"]


class TestCheckpointResume:
    async def test_the_checkpoint_alone_replays_on_a_different_path(self) -> None:
        """A fresh ledger per pass: only the checkpoint can be deduplicating."""
        effects = _Effects()
        store = InMemoryCheckpointStore()
        checkpoint = Checkpoint(run_id="run-cp", query="q")
        await store.save(checkpoint)

        await Agent(
            tools=effects.tools(),
            llm_service=_first_pass(),
            tool_ledger=InMemoryToolLedger(),
        ).run("q", checkpoint=CheckpointManager(store, checkpoint))

        loaded = await store.load("run-cp")
        assert loaded is not None and len(loaded.steps) == 2
        resumed = await Agent(
            tools=effects.tools(),
            llm_service=_resumed_pass(),
            tool_ledger=InMemoryToolLedger(),
        ).run("q", checkpoint=CheckpointManager(store, loaded))

        assert effects.ran == ["charge:10", "notify:ops"]
        replayed = resumed.messages[2].content
        assert [block.is_error for block in replayed] == [False, False]
        assert "notified ops" in replayed[0].content
        assert "charged 10" in replayed[1].content

    async def test_the_checkpoint_run_id_keys_the_ledger(self) -> None:
        """Without an explicit ``run_id`` the checkpoint's is used for the ledger."""
        effects = _Effects()
        ledger = InMemoryToolLedger()
        store = InMemoryCheckpointStore()

        for _ in range(2):
            # A brand-new checkpoint each time: only the ledger can dedupe.
            checkpoint = Checkpoint(run_id="run-shared", query="q")
            await store.save(checkpoint)
            await Agent(
                tools=effects.tools(), llm_service=_first_pass(), tool_ledger=ledger
            ).run("q", checkpoint=CheckpointManager(store, checkpoint))

        assert effects.ran == ["charge:10", "notify:ops"]
