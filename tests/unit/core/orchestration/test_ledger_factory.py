"""Which ledger a deployment gets, and whether it is told when it is degraded.

The durable ledger existed, had a migration and had tests, and nothing ever
constructed it: every site called ``InMemoryToolLedger()`` directly. That is a
worse failure than having no durable ledger at all, because the in-process one
deduplicates exactly the case a developer tests — a retry inside one worker —
and stops helping at the moment it matters, with a second replica or after a
restart.

So these tests are about the *choice*, and about the warning that accompanies a
choice the operator did not intend.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest

import core.config.orchestration as orchestration_config
from core.orchestration.idempotency import InMemoryToolLedger
from core.orchestration.ledger_factory import (
    BoundedLedger,
    DurableLedgerUnavailable,
    get_tool_ledger,
    reset_tool_ledger,
)


@pytest.fixture
def backend(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Choose the ledger backend and clear every cache that hides the choice."""

    def _set(value: str, *, postgres: bool = True) -> None:
        monkeypatch.setenv("ORCHESTRATOR_TOOL_LEDGER", value)
        monkeypatch.setattr(orchestration_config, "_orchestration_config", None)
        monkeypatch.setattr(
            "core.orchestration.ledger_factory._postgres_enabled", lambda: postgres
        )
        reset_tool_ledger()

    yield _set
    reset_tool_ledger()


def _postgres_ledger_type() -> type:
    from core.orchestration.idempotency_postgres import PostgresToolLedger

    return PostgresToolLedger


class TestTheChoice:
    def test_auto_picks_the_durable_ledger_when_postgres_is_on(
        self, backend: Any
    ) -> None:
        backend("auto", postgres=True)

        ledger = get_tool_ledger()

        assert isinstance(ledger, BoundedLedger)
        assert isinstance(ledger._inner, _postgres_ledger_type())

    def test_auto_falls_back_to_in_process_without_postgres(self, backend: Any) -> None:
        backend("auto", postgres=False)

        assert isinstance(get_tool_ledger(), InMemoryToolLedger)

    def test_memory_is_honoured_even_with_postgres_available(
        self, backend: Any
    ) -> None:
        backend("memory", postgres=True)

        assert isinstance(get_tool_ledger(), InMemoryToolLedger)

    def test_off_returns_nothing_to_record_in(self, backend: Any) -> None:
        backend("off", postgres=True)

        assert get_tool_ledger() is None


class TestFailureIsNotSilent:
    def test_demanding_postgres_fails_loudly_when_it_cannot_be_built(
        self, backend: Any
    ) -> None:
        """Silently degrading here hands back the guarantee in name only."""
        backend("postgres", postgres=True)

        with (
            patch(
                "core.orchestration.idempotency_postgres.PostgresToolLedger",
                side_effect=RuntimeError("no driver"),
            ),
            pytest.raises(DurableLedgerUnavailable, match="no driver"),
        ):
            get_tool_ledger()

    def test_auto_degrades_instead_of_failing(self, backend: Any) -> None:
        """``auto`` resolves *to* postgres, so it must not fail like a demand."""
        backend("auto", postgres=True)

        with patch(
            "core.orchestration.idempotency_postgres.PostgresToolLedger",
            side_effect=RuntimeError("no driver"),
        ):
            ledger = get_tool_ledger()

        assert isinstance(ledger, InMemoryToolLedger)

    def test_without_postgres_the_operator_is_warned(self, backend: Any) -> None:
        """Degrading in silence is how this went unnoticed for so long."""
        backend("auto", postgres=False)

        with patch("core.orchestration.ledger_factory.logger") as log:
            get_tool_ledger()

        warned = [call.args[0] for call in log.warning.call_args_list]
        assert "tool_ledger_in_process_only" in warned

    def test_switching_it_off_is_warned_too(self, backend: Any) -> None:
        backend("off", postgres=True)

        with patch("core.orchestration.ledger_factory.logger") as log:
            get_tool_ledger()

        assert "tool_ledger_disabled" in [
            call.args[0] for call in log.warning.call_args_list
        ]


class TestADeadLedgerDoesNotStallTheLoop:
    """An unreachable database must cost a bounded pause, not 30 seconds a call.

    The connection pool waits 30 seconds before giving up, and the claim path
    fails open on any ledger error — so without a deadline a deployment whose
    database is down pays that wait on *every* effectful tool call while still
    ending up unrecorded.
    """

    async def test_a_hanging_ledger_raises_instead_of_waiting(self) -> None:
        class Hangs:
            async def begin(self, key: str, *, run_id: str, tool: str) -> None:
                await asyncio.sleep(30)

        bounded = BoundedLedger(Hangs(), timeout=0.05)

        with pytest.raises(TimeoutError):
            await bounded.begin("k", run_id="r", tool="t")

    async def test_the_loop_proceeds_when_the_ledger_times_out(self) -> None:
        """Fail open: the call runs unrecorded rather than not at all."""
        from core.reasoning.react import ToolDefinition
        from core.reasoning.react_tool_gate import claim_ledger_entry

        class Hangs:
            async def begin(self, key: str, *, run_id: str, tool: str) -> None:
                await asyncio.sleep(30)

        tool = ToolDefinition(
            name="charge", fn=lambda: "x", description="d", category="mutating"
        )

        key, replayed = await claim_ledger_entry(
            BoundedLedger(Hangs(), timeout=0.05), "run-1", 0, tool, {}, occurrence=0
        )

        assert key is None and replayed is None

    async def test_a_sustained_outage_stops_being_paid_for(self) -> None:
        """The deadline bounds one call; the breaker bounds the outage.

        Without the breaker every effectful call keeps paying the full
        deadline for as long as the database is down.
        """
        from core.resilience.circuit_breaker import _circuit_breakers

        _circuit_breakers.pop("tool_ledger", None)
        attempts = {"n": 0}

        class Hangs:
            async def begin(self, key: str, *, run_id: str, tool: str) -> None:
                attempts["n"] += 1
                await asyncio.sleep(30)

        bounded = BoundedLedger(Hangs(), timeout=0.01)
        for _ in range(5):
            with pytest.raises(Exception):
                await bounded.begin("k", run_id="r", tool="t")

        _circuit_breakers.pop("tool_ledger", None)
        assert attempts["n"] < 5, "every call still reached the dead ledger"

    async def test_a_healthy_ledger_is_untouched(self) -> None:
        bounded = BoundedLedger(InMemoryToolLedger(), timeout=5.0)

        assert await bounded.begin("k", run_id="r", tool="t") is None
        await bounded.complete("k", "done")
        outcome = await bounded.lookup("k")

        assert outcome is not None and outcome.result == "done"


class TestDemandingPostgresIsNotSwallowed:
    """ "Fails loudly" has to hold everywhere, or it is not a guarantee."""

    def test_demanding_postgres_without_postgres_is_refused(self, backend: Any) -> None:
        """Construction opens no connection, so this is the detectable case."""
        backend("postgres", postgres=False)

        with pytest.raises(DurableLedgerUnavailable, match="POSTGRES_ENABLED"):
            get_tool_ledger()

    async def test_the_typed_agent_propagates_it(self, backend: Any) -> None:
        """Answering with no ledger at all is worse than the auto fallback."""
        from core.agent import Agent

        backend("postgres", postgres=False)

        with pytest.raises(DurableLedgerUnavailable):
            Agent()._ledger()


class TestTheLedgerIsShared:
    def test_one_ledger_per_process(self, backend: Any) -> None:
        """Two agents in one worker must deduplicate against each other.

        A per-agent ledger cannot, which quietly narrowed even the case the
        in-process ledger is supposed to cover.
        """
        backend("memory", postgres=False)

        assert get_tool_ledger() is get_tool_ledger()

    def test_reset_rebuilds_it(self, backend: Any) -> None:
        backend("memory", postgres=False)
        first = get_tool_ledger()
        reset_tool_ledger()

        assert get_tool_ledger() is not first

    async def test_two_agents_share_one_claim(self, backend: Any) -> None:
        """The property, not the identity: the effect happens once."""
        from core.agent import Agent
        from core.reasoning.react import ToolDefinition
        from core.services.llm.tool_calling import LLMResult, ToolCall
        from tests.unit.core.agent.test_agent import _mock_service

        backend("memory", postgres=False)
        calls = {"n": 0}

        def charge(amount: int = 1) -> str:
            calls["n"] += 1
            return "charged"

        for _ in range(2):
            service = _mock_service(
                [
                    LLMResult(
                        text="",
                        tool_calls=[
                            ToolCall(id="c1", name="charge", arguments={"amount": 10})
                        ],
                    ),
                    LLMResult(text="done"),
                ]
            )
            agent = Agent(
                tools=[
                    ToolDefinition(
                        name="charge",
                        fn=charge,
                        description="d",
                        category="external_side_effect",
                    )
                ],
                llm_service=service,
            )
            await agent.run("pay", run_id="shared-run")

        assert calls["n"] == 1
