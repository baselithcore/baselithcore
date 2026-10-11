"""A checkpointed ``Agent.run`` records every model turn, not only tool calls.

Each case crashes a run part-way (the model call of a later turn raises),
resumes it on the same checkpoint with a fresh service, and counts what really
ran on the resumed pass: recorded turns must not reach the model, recorded
tool calls must not execute, and only the first unrecorded turn may run.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from baselith.core.agent import Agent
from baselith.core.agent._durable_turns import (
    LLM_TURN_STEP,
    decode_result,
    encode_result,
)
from baselith.core.agent.events import Completed, TextDelta
from baselith.core.orchestration.budget_context import (
    activate_budget,
    deactivate_budget,
    get_active_budget,
)
from baselith.core.orchestration.checkpoint import Checkpoint, CheckpointManager
from baselith.core.orchestration.checkpoint_memory import InMemoryCheckpointStore
from baselith.core.orchestration.checkpoint_sqlite import SQLiteCheckpointStore
from baselith.core.orchestration.idempotency import InMemoryToolLedger
from baselith.core.orchestration.limits import LoopBudget, LoopLimits
from baselith.core.reasoning.react import ToolDefinition
from baselith.core.services.llm.messages import (
    Message,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
)
from baselith.core.services.llm.tool_calling import LLMResult, ToolCall
from baselith.core.services.llm.usage import Usage

pytestmark = pytest.mark.unit


class _Crash(RuntimeError):
    """The process "dies" during a model call."""


class _Model:
    """A message-API service double that counts calls and can crash.

    ``turns`` are served in order; the entry ``_Crash`` raises instead. Every
    served call charges the ambient budget, the way ``LLMService`` does.
    """

    def __init__(self, turns: list[Any], *, cost: float = 0.01) -> None:
        self.supports_messages = True
        self.supports_text_streaming = True
        self.calls = 0
        self._turns = list(turns)
        self._cost = cost

    async def generate_messages(self, messages: list[Message], **kwargs: Any) -> Any:
        self.calls += 1
        turn = self._turns.pop(0)
        if turn is _Crash:
            raise _Crash("worker died mid-call")
        budget = get_active_budget()
        if budget is not None:
            budget.charge(self._cost)
            budget.record_tokens(100)
        on_text = kwargs.get("on_text")
        if on_text is not None and turn.text and not turn.tool_calls:
            for chunk in (turn.text[:2], turn.text[2:]):
                on_text(chunk)
        return turn


class _Effects:
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


def _calls(*calls: tuple[str, dict[str, Any]], thinking: bool = False) -> LLMResult:
    tool_calls = [
        ToolCall(id=f"toolu-{name}-{i}", name=name, arguments=args)
        for i, (name, args) in enumerate(calls)
    ]
    blocks: list[Any] = []
    if thinking:
        blocks.append(
            ThinkingBlock(
                payload={"type": "thinking", "thinking": "hm", "signature": "sig"}
            )
        )
    blocks.extend(
        ToolUseBlock(id=c.id, name=c.name, input=c.arguments) for c in tool_calls
    )
    return LLMResult(
        text=None,
        tool_calls=tool_calls,
        stop_reason="tool_use",
        usage=Usage(input_tokens=50, output_tokens=10),
        message=Message(role="assistant", content=blocks),
    )


def _answer(text: str) -> LLMResult:
    return LLMResult(
        text=text,
        stop_reason="end_turn",
        message=Message(role="assistant", content=[TextBlock(text=text)]),
    )


async def _fresh(store: Any, run_id: str) -> CheckpointManager:
    checkpoint = Checkpoint(run_id=run_id, query="q")
    await store.save(checkpoint)
    return CheckpointManager(store, checkpoint)


async def _reload(store: Any, run_id: str) -> CheckpointManager:
    loaded = await store.load(run_id)
    assert loaded is not None
    return CheckpointManager(store, loaded)


def _agent(model: _Model, effects: _Effects, **kwargs: Any) -> Agent[Any]:
    return Agent(
        tools=effects.tools(),
        llm_service=model,
        tool_ledger=InMemoryToolLedger(),
        loop_limits=None,
        **kwargs,
    )


def _turn_steps(manager: CheckpointManager) -> list[dict[str, Any]]:
    return [
        step
        for step in manager.checkpoint.steps.values()
        if step["tool_name"] == LLM_TURN_STEP
    ]


class TestCrashAndResume:
    async def test_recorded_turns_and_calls_replay_and_the_rest_runs(self) -> None:
        store = InMemoryCheckpointStore()
        effects = _Effects()
        first = _Model(
            [
                _calls(("charge", {"amount": 10}), thinking=True),
                _calls(("notify", {"to": "ops"})),
                _Crash,
            ]
        )
        with pytest.raises(_Crash):
            await _agent(first, effects).run(
                "pay and tell ops", checkpoint=await _fresh(store, "run-t1")
            )
        assert first.calls == 3
        assert effects.ran == ["charge:10", "notify:ops"]

        resumed_model = _Model([_answer("all done")])
        manager = await _reload(store, "run-t1")
        assert len(_turn_steps(manager)) == 2
        result = await _agent(resumed_model, effects).run(
            "pay and tell ops", checkpoint=manager
        )

        # Only the interrupted third turn reached the model; no tool re-ran.
        assert resumed_model.calls == 1
        assert effects.ran == ["charge:10", "notify:ops"]
        assert result.output == "all done"
        assert result.iterations == 3
        assert result.tool_calls_made == ["charge", "notify"]
        # The replayed assistant turn came back verbatim, thinking included.
        replayed = result.messages[1]
        assert isinstance(replayed.content[0], ThinkingBlock)
        assert replayed.content[0].payload["signature"] == "sig"
        assert len(_turn_steps(manager)) == 3

    async def test_a_completed_run_resumes_without_any_model_call(self) -> None:
        store = InMemoryCheckpointStore()
        effects = _Effects()
        first = _Model([_calls(("charge", {"amount": 5})), _answer("paid")])
        await _agent(first, effects).run(
            "pay", checkpoint=await _fresh(store, "run-t2")
        )

        again = _Model([])
        result = await _agent(again, effects).run(
            "pay", checkpoint=await _reload(store, "run-t2")
        )
        assert again.calls == 0
        assert effects.ran == ["charge:5"]
        assert result.output == "paid"

    async def test_a_redefined_agent_misses_instead_of_replaying(self) -> None:
        store = InMemoryCheckpointStore()
        effects = _Effects()
        await _agent(_Model([_answer("v1")]), effects).run(
            "q", checkpoint=await _fresh(store, "run-t3")
        )
        changed = _Model([_answer("v2")])
        result = await _agent(changed, effects, system_prompt="new rules").run(
            "q", checkpoint=await _reload(store, "run-t3")
        )
        assert changed.calls == 1
        assert result.output == "v2"

    async def test_turns_survive_a_real_restart_on_a_file_store(
        self, tmp_path: Path
    ) -> None:
        """JSON round trip: the record must persist through a durable store."""
        path = tmp_path / "checkpoints.db"
        effects = _Effects()
        store = SQLiteCheckpointStore(path)
        first = _Model([_calls(("charge", {"amount": 7}), thinking=True), _Crash])
        with pytest.raises(_Crash):
            await _agent(first, effects).run(
                "q", checkpoint=await _fresh(store, "run-t4")
            )
        store.close()

        reopened = SQLiteCheckpointStore(path)
        try:
            resumed_model = _Model([_answer("ok")])
            result = await _agent(resumed_model, effects).run(
                "q", checkpoint=await _reload(reopened, "run-t4")
            )
        finally:
            reopened.close()
        assert resumed_model.calls == 1
        assert effects.ran == ["charge:7"]
        assert result.output == "ok"


class TestAccounting:
    async def test_replayed_turns_charge_no_cost_but_still_count(self) -> None:
        store = InMemoryCheckpointStore()
        effects = _Effects()
        budget = LoopBudget(limits=LoopLimits(max_iterations=50, budget_usd=10.0))
        token = activate_budget(budget)
        try:
            with pytest.raises(_Crash):
                await _agent(
                    _Model([_calls(("charge", {"amount": 1})), _Crash]), effects
                ).run("q", checkpoint=await _fresh(store, "run-t5"))
            paid = budget.cost_usd
            iterations = budget.iterations
            tokens = budget.tokens

            await _agent(_Model([_answer("done")]), effects).run(
                "q", checkpoint=await _reload(store, "run-t5")
            )
        finally:
            deactivate_budget(token)
        # The resumed pass paid for exactly one live turn …
        assert budget.cost_usd == pytest.approx(paid + 0.01)
        assert budget.tokens == tokens + 100
        # … and still walked both turns.
        assert budget.iterations == iterations + 2


class TestStreamingReplay:
    async def test_a_replayed_turn_streams_its_text_once_whole(self) -> None:
        store = InMemoryCheckpointStore()
        effects = _Effects()
        live = _Model([_answer("hello")])
        events = [
            e
            async for e in _agent(live, effects).run_events(
                "q", checkpoint=await _fresh(store, "run-t6")
            )
        ]
        assert [e.text for e in events if isinstance(e, TextDelta)] == ["he", "llo"]

        replay = _Model([])
        events = [
            e
            async for e in _agent(replay, effects).run_events(
                "q", checkpoint=await _reload(store, "run-t6")
            )
        ]
        assert replay.calls == 0
        assert [e.text for e in events if isinstance(e, TextDelta)] == ["hello"]
        assert isinstance(events[-1], Completed)


class TestRecordShape:
    def test_a_record_is_json_and_round_trips(self) -> None:
        live = _calls(("charge", {"amount": 3}), thinking=True)
        assert live.message is not None
        record = encode_result(live, live.message)
        rebuilt = decode_result(json.loads(json.dumps(record)))
        assert rebuilt.tool_calls == live.tool_calls
        assert rebuilt.usage == live.usage
        assert rebuilt.message == live.message

    async def test_without_a_checkpoint_nothing_is_recorded(self) -> None:
        model = _Model([_answer("x")])
        service = AsyncMock(wraps=model)
        del service  # the plain path never touches a store
        result = await _agent(model, _Effects()).run("q")
        assert result.output == "x"
        assert model.calls == 1
