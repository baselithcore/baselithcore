"""Fail-closed defaults for ReAct and parallel tool execution."""

from core.orchestration.autonomy import DESTRUCTIVE, AutonomyLevel, AutonomyPolicy
from core.orchestration.parallel import ParallelToolExecutor
from core.reasoning.react import ReActAgent
from core.reasoning.react_types import ToolDefinition


def test_tool_definition_category_defaults_to_destructive():
    td = ToolDefinition(name="t", fn=lambda: "x", description="d")
    assert td.category == DESTRUCTIVE


def test_parallel_executor_defaults_to_supervised_policy():
    executor = ParallelToolExecutor()
    assert executor.autonomy_policy is not None
    assert executor.autonomy_policy.level == AutonomyLevel.SUPERVISED


def test_react_agent_defaults_to_supervised_policy():
    agent = ReActAgent(tools=[])
    assert agent._autonomy_policy is not None
    assert agent._autonomy_policy.level == AutonomyLevel.SUPERVISED


def test_explicit_policy_still_wins():
    policy = AutonomyPolicy(level=AutonomyLevel.FULLY_AUTONOMOUS)
    agent = ReActAgent(tools=[], autonomy_policy=policy)
    assert agent._autonomy_policy is policy
    executor = ParallelToolExecutor(autonomy_policy=policy)
    assert executor.autonomy_policy is policy
