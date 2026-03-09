"""
Tests for MyAgent.

Run with: pytest tests/
"""

import pytest
from agent import MyAgent
from tools import search_knowledge_base, get_current_time, calculate, get_all_tools


class TestAgent:
    """Test agent functionality."""

    @pytest.fixture
    def agent(self):
        """Create agent instance for testing."""
        return MyAgent(agent_id="test-agent", llm_service=None)

    def test_agent_id(self, agent):
        """Verify agent_id is set."""
        assert agent.agent_id == "test-agent"

    def test_tools_initialized(self, agent):
        """Verify tools list is initialized."""
        assert isinstance(agent.tools, list)

    def test_add_tool(self, agent):
        """Test adding tool to agent."""
        def dummy_tool():
            pass

        agent.add_tool(dummy_tool)
        assert dummy_tool in agent.tools


class TestTools:
    """Test tool functions."""

    def test_search_knowledge_base(self):
        """Test KB search returns results."""
        results = search_knowledge_base("test query")
        assert isinstance(results, list)

    def test_get_current_time(self):
        """Test time function returns ISO format."""
        result = get_current_time()
        assert "T" in result  # ISO format contains 'T'

    def test_calculate_simple(self):
        """Test simple calculation."""
        assert calculate("2 + 2") == 4

    def test_calculate_complex(self):
        """Test complex calculation."""
        assert calculate("(10 + 5) * 2") == 30

    def test_get_all_tools(self):
        """Test tool discovery."""
        tools = get_all_tools()
        assert len(tools) >= 3
        tool_names = [t._tool_name for t in tools]
        assert "search_knowledge_base" in tool_names
