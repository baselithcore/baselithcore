"""
Tests for Memory-Aware Swarm Agents
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from core.orchestration.handlers.swarm_handler import SwarmHandler
from core.swarm.colony import Colony
from core.swarm.types import AgentProfile, Capability

@pytest.mark.asyncio
async def test_execute_with_agent_fetches_memory():
    # Setup mocks
    mock_llm = AsyncMock()
    mock_memory = AsyncMock()
    mock_memory.recall.return_value = [
        MagicMock(content="Memory 1"),
        MagicMock(content="Memory 2")
    ]
    mock_memory.graph_provider = AsyncMock()
    mock_memory.graph_provider.query_graph.return_value = [
        {"source": "A", "relation": "rel", "target": "B"}
    ]
    
    colony = Colony(memory_manager=mock_memory)
    handler = SwarmHandler(llm_service=mock_llm)
    handler._colony = colony
    
    agent = AgentProfile(id="virtual_research", name="Researcher")
    task_def = {"description": "Test task", "capability": "research"}
    
    # Execute
    await handler._execute_with_agent(task_def, agent)
    
    # Verify memory was recalled
    mock_memory.recall.assert_called_once()
    # Verify graph was queried
    mock_memory.graph_provider.query_graph.assert_called_once()

    # Verify LLM was called with memory context in prompt
    call_args = mock_llm.generate_response.call_args[0][0]
    assert "Memory 1" in call_args
    assert "A rel B" in call_args
