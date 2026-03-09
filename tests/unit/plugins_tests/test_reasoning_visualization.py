import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Add plugins to path for testing
plugin_path = Path(__file__).parent.parent.parent.parent / "plugins"
if str(plugin_path) not in sys.path:
    sys.path.insert(0, str(plugin_path))

from reasoning_agent.reasoning_agent import ReasoningAgent  # noqa: E402
from core.reasoning.tot import ThoughtNode  # noqa: E402


@pytest.mark.asyncio
async def test_reasoning_agent_returns_tree():
    # Mock LLM Service
    mock_llm = MagicMock()
    # Mock TreeOfThoughtsAsync inside the agent

    agent = ReasoningAgent(service=mock_llm)

    # We mock the engine directly to control output
    # Mock return value: dict with steps and visualization
    mock_root = ThoughtNode(content="Start", score=1.0)
    child = ThoughtNode(content="Step 1", score=0.9, parent=mock_root)
    mock_root.children.append(child)

    path = ["Start", "Step 1"]

    agent.tot_engine.solve = AsyncMock()
    # solve returns a dict
    agent.tot_engine.solve.return_value = {
        "solution": "Step 1",
        "best_solution": "Step 1",
        "steps": path,
        "tree_visualization": "graph TD...",
        "tree_data": mock_root.to_dict(),
    }

    # Exec
    result = await agent.solve("Solve X")

    assert isinstance(result, dict)
    assert "best_solution" in result
    assert "tree_data" in result
    assert result["tree_data"]["content"] == "Start"
    assert len(result["tree_data"]["children"]) == 1
    assert result["tree_data"]["children"][0]["content"] == "Step 1"

    # Verify call arguments
    agent.tot_engine.solve.assert_called_with(
        problem="Solve X",
        k=3,
        max_steps=5,
        tools=[],  # default tools
    )
