"""Crash-and-resume for the plugin agents: completed work is replayed, never redone.

Each test runs an agent with the run's checkpoint bound as ambient (as the orchestrator does
around a flow handler), crashes it mid-way with a ``BaseException`` the agent cannot swallow,
then resumes with a fresh manager loaded from the same store and counts what ran again.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from baselith.core.orchestration.checkpoint import Checkpoint, CheckpointManager
from baselith.core.orchestration.checkpoint_memory import InMemoryCheckpointStore
from baselith.core.orchestration.checkpoint_scope import bind_checkpoint
from baselith.plugins.browser_agent.agent import BrowserAgent
from baselith.plugins.browser_agent.agent.durable import INTERRUPTED_ERROR
from baselith.plugins.browser_agent.types import (
    BrowserAction,
    BrowserActionType,
    BrowserAgentResult,
    PageState,
)
from baselith.plugins.coding_agent import CodingAgent, Workspace
from baselith.plugins.reasoning_agent.plugin import ReasoningFlowHandler

from .coding_agent_fakes import ADD_TESTS, BAD_ADD, GOOD_ADD, LocalSandbox, ScriptedLLM

WRONG_ADD = "def add(a: int, b: int) -> int:\n    return a * b\n"


class Crash(BaseException):
    """A process dying mid-run: nothing in the agent may catch it."""


async def _fresh(
    store: InMemoryCheckpointStore, run_id: str = "run-1"
) -> CheckpointManager:
    checkpoint = Checkpoint(run_id=run_id)
    await store.save(checkpoint)
    return CheckpointManager(store, checkpoint)


async def _resumed(
    store: InMemoryCheckpointStore, run_id: str = "run-1"
) -> CheckpointManager:
    loaded = await store.load(run_id)
    assert loaded is not None
    return CheckpointManager(store, loaded)


class CrashingSandbox(LocalSandbox):
    """Runs for real until call ``crash_on`` (1-based), which kills the 'process'."""

    def __init__(self, crash_on: int | None = None) -> None:
        super().__init__()
        self.crash_on = crash_on

    async def execute_code_async(self, code: str, *args: Any, **kwargs: Any) -> Any:
        if self.crash_on is not None and self.calls + 1 == self.crash_on:
            self.calls += 1
            raise Crash
        return await super().execute_code_async(code, *args, **kwargs)


def _coding(
    answers: list[Any], sandbox: LocalSandbox
) -> tuple[CodingAgent, ScriptedLLM]:
    agent = CodingAgent(max_fix_attempts=5, execution_timeout=60)
    llm = ScriptedLLM(answers)
    agent._llm, agent._sandbox = llm, sandbox
    return agent, llm


def _both(code: str) -> dict[str, Any]:
    return {"code": code, "tests": ADD_TESTS, "explanation": "x", "changes": ["c"]}


def _code(code: str) -> dict[str, Any]:
    return {"code": code, "explanation": "y", "changes": ["c"]}


# -- coding agent -----------------------------------------------------------------------------


async def test_coding_fix_resumes_without_redoing_completed_attempts() -> None:
    store = InMemoryCheckpointStore()
    answers = [_both(WRONG_ADD), _code(GOOD_ADD)]

    # Pass 1: answer 1, run 1 (red), answer 2, then the process dies in run 2.
    agent, llm = _coding(list(answers), CrashingSandbox(crash_on=2))
    with bind_checkpoint(await _fresh(store)), pytest.raises(Crash):
        await agent.fix_code(BAD_ADD, "wrong sum")
    assert len(llm.prompts) == 2

    # Pass 2: both answers and the first run replay; only run 2 and the reproduction run execute.
    agent2, llm2 = _coding([], CrashingSandbox())
    with bind_checkpoint(await _resumed(store)):
        result = await agent2.fix_code(BAD_ADD, "wrong sum")
    assert llm2.prompts == []  # no model call repeated
    assert agent2._sandbox.calls == 2
    assert result.success and result.final_code == GOOD_ADD and result.iterations == 2


async def test_coding_finished_task_replays_entirely() -> None:
    store = InMemoryCheckpointStore()
    agent, _ = _coding([_both(GOOD_ADD)], LocalSandbox())
    with bind_checkpoint(await _fresh(store)):
        first = await agent.fix_code(BAD_ADD, "wrong sum")

    agent2, llm2 = _coding([], LocalSandbox())
    with bind_checkpoint(await _resumed(store)):
        again = await agent2.fix_code(BAD_ADD, "wrong sum")
    assert llm2.prompts == [] and agent2._sandbox.calls == 0
    assert again.final_code == first.final_code and again.success
    assert again.tests_passed == first.tests_passed


async def test_coding_without_checkpoint_records_nothing() -> None:
    store = InMemoryCheckpointStore()
    manager = await _fresh(store)
    agent, llm = _coding([_both(GOOD_ADD)], LocalSandbox())
    result = await agent.fix_code(BAD_ADD, "wrong sum")  # nothing bound
    assert result.success and len(llm.prompts) == 1
    assert manager.checkpoint.steps == {}


UTIL = "def double(x):\n    return 2 * x\n"
BUGGY = "from pkg.util import double\n\n\ndef quad(x):\n    return double(x) + 1\n"
FIXED = "from pkg.util import double\n\n\ndef quad(x):\n    return double(double(x))\n"
WS_TESTS = "from pkg.mod import quad\n\n\ndef test_quad():\n    assert quad(1) == 4\n"
WS = Workspace(
    {"pkg/util.py": UTIL, "pkg/mod.py": BUGGY}, "pkg/mod.py", "tests/test_q.py"
)


async def test_workspace_fix_resumes_after_the_probe_and_the_answer() -> None:
    store = InMemoryCheckpointStore()
    answer = {"code": FIXED, "tests": WS_TESTS, "explanation": "n", "changes": []}

    # Pass 1: probe runs, the model answers, then the process dies in the first test run.
    agent, llm = _coding([answer], CrashingSandbox(crash_on=2))
    with bind_checkpoint(await _fresh(store)), pytest.raises(Crash):
        await agent.fix_in_workspace(WS, "quad(1) returns 3")
    assert len(llm.prompts) == 1

    agent2, llm2 = _coding([], CrashingSandbox())
    with bind_checkpoint(await _resumed(store)):
        out = await agent2.fix_in_workspace(WS, "quad(1) returns 3")
    assert llm2.prompts == []
    assert agent2._sandbox.calls == 2  # the test run and the reproduction run; no probe
    assert out.success and out.verified and out.final_code == FIXED


# -- browser agent ----------------------------------------------------------------------------


def _page() -> PageState:
    return PageState("https://example.com", "t", "AAAA", 1280, 720)


def _browser(actions: list[BrowserAction]) -> BrowserAgent:
    agent = BrowserAgent(vision_service=MagicMock())
    agent._page = MagicMock(url="https://example.com")
    agent.get_page_state = AsyncMock(return_value=_page())  # type: ignore[method-assign]
    agent.decide_next_action = AsyncMock(side_effect=actions)  # type: ignore[method-assign]
    agent.execute_action = AsyncMock(return_value=True)  # type: ignore[method-assign]
    return agent


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr("baselith.plugins.browser_agent.agent.asyncio.sleep", _instant)


async def test_browser_finished_task_replays_without_a_browser(no_sleep: None) -> None:
    store = InMemoryCheckpointStore()
    done = BrowserAction(BrowserActionType.DONE, reasoning="ok")
    agent = _browser([BrowserAction(BrowserActionType.SCROLL, value="down"), done])
    with bind_checkpoint(await _fresh(store)):
        first = await agent.execute_task("read the page")
    assert first.success and first.steps_taken == 2

    agent2 = _browser([])
    with bind_checkpoint(await _resumed(store)):
        again = await agent2.execute_task("read the page")
    assert again.success and again.steps_taken == 2 and again.screenshots == []
    agent2.decide_next_action.assert_not_called()
    agent2.execute_action.assert_not_called()


async def test_browser_interrupted_after_a_click_is_never_rerun(no_sleep: None) -> None:
    store = InMemoryCheckpointStore()
    click = BrowserAction(BrowserActionType.CLICK, selector="#buy")
    agent = _browser([click])
    agent.execute_action = AsyncMock(side_effect=Crash)  # type: ignore[method-assign]
    with bind_checkpoint(await _fresh(store)), pytest.raises(Crash):
        await agent.execute_task("buy it")

    agent2 = _browser([click, BrowserAction(BrowserActionType.DONE)])
    with bind_checkpoint(await _resumed(store)):
        result = await agent2.execute_task("buy it")
    assert not result.success and result.error == INTERRUPTED_ERROR
    agent2.execute_action.assert_not_called()  # at most once: the click is not repeated


async def test_browser_interrupted_while_only_reading_reruns(no_sleep: None) -> None:
    store = InMemoryCheckpointStore()
    agent = _browser([BrowserAction(BrowserActionType.SCROLL, value="down")])
    agent.execute_action = AsyncMock(side_effect=Crash)  # type: ignore[method-assign]
    with bind_checkpoint(await _fresh(store)), pytest.raises(Crash):
        await agent.execute_task("read it")

    agent2 = _browser([BrowserAction(BrowserActionType.DONE, reasoning="read")])
    with bind_checkpoint(await _resumed(store)):
        result = await agent2.execute_task("read it")
    assert result.success


async def test_browser_without_checkpoint_is_unchanged(no_sleep: None) -> None:
    agent = _browser([BrowserAction(BrowserActionType.DONE)])
    result = await agent.execute_task("t")
    assert isinstance(result, BrowserAgentResult) and result.success


# -- reasoning agent --------------------------------------------------------------------------


def _reasoning(solve: AsyncMock) -> ReasoningFlowHandler:
    agent = MagicMock()
    agent.solve = solve
    return ReasoningFlowHandler(agent)


_SOLUTION = {"best_solution": "42", "steps": ["a", "b"], "tree_visualization": "g"}


async def test_reasoning_completed_search_is_not_repeated() -> None:
    store = InMemoryCheckpointStore()
    solve = AsyncMock(return_value=_SOLUTION)
    handler = _reasoning(solve)
    manager = await _fresh(store)
    first = await handler.handle("q", {"checkpoint": manager})
    assert first["content"] == "42" and solve.await_count == 1

    # The process dies after the search but before the run completes; the resume replays it.
    solve2 = AsyncMock(side_effect=AssertionError("searched twice"))
    again = await _reasoning(solve2).handle("q", {"checkpoint": await _resumed(store)})
    assert again["content"] == "42" and again["metadata"]["steps"] == ["a", "b"]
    solve2.assert_not_awaited()


async def test_reasoning_crashed_search_runs_again_on_resume() -> None:
    store = InMemoryCheckpointStore()
    handler = _reasoning(AsyncMock(side_effect=Crash))
    with pytest.raises(Crash):
        await handler.handle("q", {"checkpoint": await _fresh(store)})

    solve = AsyncMock(return_value=_SOLUTION)
    result = await _reasoning(solve).handle("q", {"checkpoint": await _resumed(store)})
    assert result["content"] == "42" and solve.await_count == 1


async def test_reasoning_without_checkpoint_is_unchanged() -> None:
    solve = AsyncMock(return_value=_SOLUTION)
    result = await _reasoning(solve).handle("q", {})
    assert result["content"] == "42" and solve.await_count == 1
