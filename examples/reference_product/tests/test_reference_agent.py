"""
Tests for the Reference Product FAQ Agent.
"""

import pytest
from core.lifecycle import AgentState, AgentError, FrameworkErrorCode
from examples.reference_product.agent import FAQAgent

@pytest.mark.asyncio
async def test_agent_lifecycle_flow():
    """Test full lifecycle flow: Init -> Start -> Run -> Stop."""
    kb = {"ping": "pong"}
    agent = FAQAgent(kb)
    
    assert agent.state == AgentState.UNINITIALIZED
    
    # Startup
    await agent.startup()
    assert agent.state == AgentState.READY
    
    # Execute
    res = await agent.execute("ping")
    assert res == "pong"
    
    # Shutdown
    await agent.shutdown()
    assert agent.state == AgentState.STOPPED

@pytest.mark.asyncio
async def test_agent_not_ready_error():
    """Verify error when executing before startup."""
    agent = FAQAgent({})
    
    with pytest.raises(AgentError) as exc:
        await agent.execute("ping")
    
    assert exc.value.code == FrameworkErrorCode.AGENT_NOT_READY
