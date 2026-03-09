"""
Unit Tests for Parallel Tool Execution.
"""

import pytest
import asyncio
from core.orchestration.parallel import ParallelToolExecutor, ToolCall


class TestParallelToolExecutor:
    """Tests for ParallelToolExecutor class."""

    @pytest.mark.asyncio
    async def test_register_and_execute_simple(self):
        """Test basic tool registration and execution."""
        executor = ParallelToolExecutor()

        async def mock_tool(arg):
            return f"Processed {arg}"

        executor.register_tool("test_tool", mock_tool)

        call = ToolCall(tool_name="test_tool", parameters={"arg": "data"})
        results = await executor.execute_parallel([call])

        assert len(results) == 1
        assert results[0].success is True
        assert results[0].result == "Processed data"

    def test_analyze_dependencies_independent(self):
        """Test analyzing calls with no dependencies."""
        executor = ParallelToolExecutor()
        calls = [
            ToolCall(id="1", tool_name="t1"),
            ToolCall(id="2", tool_name="t2"),
        ]

        plan = executor.analyze_dependencies(calls)

        assert len(plan.parallel_groups) == 1
        assert len(plan.parallel_groups[0]) == 2
        assert "1" in plan.parallel_groups[0]
        assert "2" in plan.parallel_groups[0]
        assert plan.parallelization_factor == 2.0

    def test_analyze_dependencies_dependent(self):
        """Test analyzing calls with dependencies."""
        executor = ParallelToolExecutor()
        calls = [
            ToolCall(id="1", tool_name="t1"),
            ToolCall(id="2", tool_name="t2", dependencies=["1"]),
        ]

        plan = executor.analyze_dependencies(calls)

        # Should be two groups: ["1"], then ["2"]
        assert len(plan.parallel_groups) == 2
        assert plan.parallel_groups[0] == ["1"]
        assert plan.parallel_groups[1] == ["2"]
        assert plan.parallelization_factor == 1.0

    @pytest.mark.asyncio
    async def test_execute_parallel_flow(self):
        """Test actual parallel execution timing."""
        executor = ParallelToolExecutor()

        async def slow_tool(delay):
            await asyncio.sleep(delay)
            return delay

        executor.register_tool("slow", slow_tool)

        # Two independent calls taking 0.1s each
        calls = [
            ToolCall(id="1", tool_name="slow", parameters={"delay": 0.1}),
            ToolCall(id="2", tool_name="slow", parameters={"delay": 0.1}),
        ]

        import time

        start = time.perf_counter()
        results = await executor.execute_parallel(calls)
        elapsed = time.perf_counter() - start

        assert len(results) == 2
        assert all(r.success for r in results)
        # Should take roughly 0.1s, not 0.2s
        assert elapsed < 0.18  # Allow some overhead

    @pytest.mark.asyncio
    async def test_execute_with_failure(self):
        """Test handling of tool failures."""
        executor = ParallelToolExecutor()

        async def failing_tool():
            raise ValueError("Boom")

        executor.register_tool("fail", failing_tool)

        calls = [ToolCall(id="1", tool_name="fail")]
        results = await executor.execute_parallel(calls)

        assert len(results) == 1
        assert results[0].success is False
        assert "Boom" in results[0].error

    @pytest.mark.asyncio
    async def test_unknown_tool(self):
        """Test calling unregistered tool."""
        executor = ParallelToolExecutor()
        calls = [ToolCall(id="1", tool_name="unknown")]
        results = await executor.execute_parallel(calls)

        assert results[0].success is False
        assert "Unknown tool" in results[0].error
