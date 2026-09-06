"""The typed ``Agent`` loop consults a tool ledger before effectful calls.

The property under test is not "the ledger is called" but "the side effect
happens once": every test counts real invocations of the tool, then re-runs the
same ``run_id`` and asserts the count did not move.
"""

import pytest

from core.agent import Agent
from core.orchestration.idempotency import InMemoryToolLedger, ToolOutcome
from core.reasoning.react import ToolDefinition
from core.services.llm.tool_calling import LLMResult, ToolCall
from tests.unit.core.agent.test_agent import _mock_service


class _Counter:
    """A tool that records how many times it actually ran."""

    def __init__(self, returns: str = "charged") -> None:
        self.calls = 0
        self.returns = returns

    def __call__(self, amount: int = 1) -> str:
        self.calls += 1
        return self.returns


def _charging_service(tool_name: str = "charge_card"):
    """One tool round-trip, then a final answer."""
    return _mock_service(
        [
            LLMResult(
                text="",
                tool_calls=[
                    ToolCall(id="c1", name=tool_name, arguments={"amount": 10})
                ],
            ),
            LLMResult(text="done"),
        ]
    )


def _tool(fn, name: str, category: str) -> ToolDefinition:
    return ToolDefinition(name=name, fn=fn, description="d", category=category)


class TestReplay:
    @pytest.mark.asyncio
    async def test_same_run_id_replays_instead_of_re_executing(self):
        effect = _Counter()
        ledger = InMemoryToolLedger()

        for _ in range(2):
            agent = Agent(
                tools=[_tool(effect, "charge_card", "external_side_effect")],
                llm_service=_charging_service(),
                tool_ledger=ledger,
            )
            result = await agent.run("pay the invoice", run_id="run-1")
            assert result.output == "done"

        assert effect.calls == 1

    @pytest.mark.asyncio
    async def test_a_different_run_id_executes_again(self):
        """Two genuinely different runs are two calls, not a dedup."""
        effect = _Counter()
        ledger = InMemoryToolLedger()

        for run_id in ("run-1", "run-2"):
            agent = Agent(
                tools=[_tool(effect, "charge_card", "external_side_effect")],
                llm_service=_charging_service(),
                tool_ledger=ledger,
            )
            await agent.run("pay the invoice", run_id=run_id)

        assert effect.calls == 2

    @pytest.mark.asyncio
    async def test_replayed_output_is_fed_back_to_the_model(self):
        effect = _Counter(returns="receipt-42")
        ledger = InMemoryToolLedger()
        for _ in range(2):
            svc = _charging_service()
            agent = Agent(
                tools=[_tool(effect, "charge_card", "external_side_effect")],
                llm_service=svc,
                tool_ledger=ledger,
            )
            await agent.run("pay", run_id="run-1")
        # Second run: the model still sees the tool result, from the ledger.
        second_turn_prompt = svc.generate.await_args_list[1].args[0]
        assert "receipt-42" in second_turn_prompt

    @pytest.mark.asyncio
    async def test_non_string_result_survives_the_replay(self):
        def lookup(amount: int = 1) -> dict[str, int]:
            return {"order": 7}

        ledger = InMemoryToolLedger()
        outputs = []
        for _ in range(2):
            svc = _charging_service("lookup")
            agent = Agent(
                tools=[_tool(lookup, "lookup", "mutating")],
                llm_service=svc,
                tool_ledger=ledger,
            )
            await agent.run("q", run_id="run-1")
            outputs.append(svc.generate.await_args_list[1].args[0])
        assert '{"order": 7}' in outputs[0]
        assert '{"order": 7}' in outputs[1]


class TestWhenTheLedgerIsBypassed:
    @pytest.mark.asyncio
    async def test_read_only_tools_are_never_ledgered(self):
        """A read costs a round trip on replay, not a defect — let it run."""
        effect = _Counter()
        ledger = InMemoryToolLedger()

        for _ in range(2):
            agent = Agent(
                tools=[_tool(effect, "charge_card", "read_only")],
                llm_service=_charging_service(),
                tool_ledger=ledger,
            )
            await agent.run("look it up", run_id="run-1")

        assert effect.calls == 2

    @pytest.mark.asyncio
    async def test_without_a_run_id_nothing_is_deduplicated(self):
        """No stable id, nothing to match: the loop must not invent one."""
        effect = _Counter()
        ledger = InMemoryToolLedger()

        for _ in range(2):
            agent = Agent(
                tools=[_tool(effect, "charge_card", "external_side_effect")],
                llm_service=_charging_service(),
                tool_ledger=ledger,
            )
            await agent.run("pay the invoice")

        assert effect.calls == 2

    @pytest.mark.asyncio
    async def test_without_a_ledger_the_loop_is_unchanged(self):
        effect = _Counter()
        for _ in range(2):
            agent = Agent(
                tools=[_tool(effect, "charge_card", "external_side_effect")],
                llm_service=_charging_service(),
            )
            await agent.run("pay", run_id="run-1")
        assert effect.calls == 2

    @pytest.mark.asyncio
    async def test_a_plain_callable_defaults_to_ledgered(self):
        """Fail-closed: an undeclared tool is effectful, so it is recorded."""
        effect = _Counter()
        ledger = InMemoryToolLedger()

        def charge_card(amount: int = 1) -> str:
            return effect()

        for _ in range(2):
            agent = Agent(
                tools=[charge_card],
                llm_service=_charging_service(),
                tool_ledger=ledger,
            )
            await agent.run("pay", run_id="run-1")

        assert effect.calls == 1


class TestFailureAndContention:
    @pytest.mark.asyncio
    async def test_a_failed_call_is_retried_on_the_next_run(self):
        """The effect did not land, so the key must not stay claimed."""
        attempts = {"n": 0}

        def flaky(amount: int = 1) -> str:
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("gateway down")
            return "charged"

        ledger = InMemoryToolLedger()
        outputs = []
        for _ in range(2):
            svc = _charging_service("flaky")
            agent = Agent(
                tools=[_tool(flaky, "flaky", "external_side_effect")],
                llm_service=svc,
                tool_ledger=ledger,
            )
            await agent.run("pay", run_id="run-1")
            outputs.append(svc.generate.await_args_list[1].args[0])

        assert attempts["n"] == 2
        assert "gateway down" in outputs[0]
        assert "charged" in outputs[1]

    @pytest.mark.asyncio
    async def test_an_in_flight_claim_is_reported_not_re_executed(self):
        """A crashed holder leaves ``in_flight``; the effect may have landed."""
        effect = _Counter()
        ledger = InMemoryToolLedger()

        class _Crashed:
            """A ledger whose key is held by a worker that never finished."""

            async def lookup(self, key):
                return ToolOutcome(status="in_flight")

            async def begin(self, key, *, run_id, tool):
                return ToolOutcome(status="in_flight")

            async def complete(self, key, result):  # pragma: no cover - unreachable
                raise AssertionError("must not complete a call it never made")

            async def fail(self, key, error):  # pragma: no cover - unreachable
                raise AssertionError("must not fail a call it never made")

        svc = _charging_service()
        agent = Agent(
            tools=[_tool(effect, "charge_card", "external_side_effect")],
            llm_service=svc,
            tool_ledger=_Crashed(),
        )
        await agent.run("pay", run_id="run-1")

        assert effect.calls == 0
        assert "already in flight" in svc.generate.await_args_list[1].args[0]
        assert await ledger.lookup("unused") is None

    @pytest.mark.asyncio
    async def test_two_identical_calls_in_one_run_both_execute(self):
        """Same tool, same arguments, different step: two distinct calls."""
        effect = _Counter()
        svc = _mock_service(
            [
                LLMResult(
                    text="",
                    tool_calls=[
                        ToolCall(id="c1", name="charge_card", arguments={"amount": 10}),
                        ToolCall(id="c1", name="charge_card", arguments={"amount": 10}),
                    ],
                ),
                LLMResult(text="done"),
            ]
        )
        agent = Agent(
            tools=[_tool(effect, "charge_card", "external_side_effect")],
            llm_service=svc,
            tool_ledger=InMemoryToolLedger(),
        )
        await agent.run("pay twice", run_id="run-1")

        assert effect.calls == 2
