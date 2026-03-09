"""
Unit Tests for Core Planning Module

Tests for task planning and decomposition.
"""

import pytest
from unittest.mock import patch
from core.planning import (
    TaskPlanner,
    Plan,
    PlanStep,
    TaskDecomposer,
)
from core.planning.planner import StepStatus


# ============================================================================
# PlanStep Tests
# ============================================================================


class TestPlanStep:
    """Tests for PlanStep dataclass."""

    def test_creation(self):
        """Basic step creation."""
        step = PlanStep(
            id="step1",
            description="Test step",
            action="execute",
        )

        assert step.id == "step1"
        assert step.status == StepStatus.PENDING
        assert step.dependencies == []

    def test_is_ready_no_deps(self):
        """Step with no deps is always ready."""
        step = PlanStep(id="s1", description="test", action="do")

        assert step.is_ready(set()) is True

    def test_is_ready_with_deps(self):
        """Step with unmet deps is not ready."""
        step = PlanStep(
            id="s1",
            description="test",
            action="do",
            dependencies=["dep1", "dep2"],
        )

        assert step.is_ready(set()) is False
        assert step.is_ready({"dep1"}) is False
        assert step.is_ready({"dep1", "dep2"}) is True


# ============================================================================
# Plan Tests
# ============================================================================


class TestPlan:
    """Tests for Plan dataclass."""

    def test_creation(self):
        """Basic plan creation."""
        plan = Plan(goal="Test goal")

        assert plan.goal == "Test goal"
        assert plan.steps == []

    def test_is_complete_empty(self):
        """Empty plan is complete."""
        plan = Plan(goal="test")

        assert plan.is_complete is True

    def test_is_complete_pending(self):
        """Plan with pending steps is not complete."""
        plan = Plan(
            goal="test",
            steps=[PlanStep(id="s1", description="test", action="do")],
        )

        assert plan.is_complete is False

    def test_is_complete_all_done(self):
        """Plan with all completed steps is complete."""
        step = PlanStep(id="s1", description="test", action="do")
        step.status = StepStatus.COMPLETED
        plan = Plan(goal="test", steps=[step])

        assert plan.is_complete is True

    def test_progress_calculation(self):
        """Progress percentage calculation."""
        s1 = PlanStep(id="s1", description="test", action="do")
        s2 = PlanStep(id="s2", description="test", action="do")
        s1.status = StepStatus.COMPLETED

        plan = Plan(goal="test", steps=[s1, s2])

        assert plan.progress == 0.5

    def test_get_next_steps(self):
        """Get ready steps."""
        s1 = PlanStep(id="s1", description="first", action="do")
        s2 = PlanStep(id="s2", description="second", action="do", dependencies=["s1"])
        plan = Plan(goal="test", steps=[s1, s2])

        next_steps = plan.get_next_steps()

        assert len(next_steps) == 1
        assert next_steps[0].id == "s1"


# ============================================================================
# TaskPlanner Tests
# ============================================================================


class TestTaskPlanner:
    """Tests for TaskPlanner."""

    def test_initialization(self):
        """Basic initialization."""
        planner = TaskPlanner()

        assert planner._llm_service is None

    @pytest.mark.asyncio
    @patch("core.services.llm.get_llm_service", side_effect=ImportError)
    async def test_create_plan_simple(self, mock_get):
        """Create simple plan without LLM."""
        planner = TaskPlanner()
        planner._llm_service = None

        plan = await planner.create_plan("Build a website")

        assert plan.goal == "Build a website"
        assert len(plan.steps) > 0

    @pytest.mark.asyncio
    @patch("core.services.llm.get_llm_service", side_effect=ImportError)
    async def test_create_plan_has_dependencies(self, mock_get):
        """Created plan has proper dependencies."""
        planner = TaskPlanner()

        plan = await planner.create_plan("Complex task")

        # Later steps should depend on earlier ones
        has_deps = any(s.dependencies for s in plan.steps)
        assert has_deps

    @pytest.mark.asyncio
    @patch("core.services.llm.get_llm_service", side_effect=ImportError)
    async def test_validate_plan_valid(self, mock_get):
        """Validate a valid plan."""
        planner = TaskPlanner()
        plan = await planner.create_plan("test")

        issues = planner.validate_plan(plan)

        assert len(issues) == 0

    def test_validate_plan_empty(self):
        """Validate empty plan reports issue."""
        planner = TaskPlanner()
        plan = Plan(goal="test")

        issues = planner.validate_plan(plan)

        assert any("no steps" in i.lower() for i in issues)

    def test_validate_plan_bad_dependency(self):
        """Validate reports unknown dependencies."""
        planner = TaskPlanner()
        step = PlanStep(
            id="s1",
            description="test",
            action="do",
            dependencies=["nonexistent"],
        )
        plan = Plan(goal="test", steps=[step])

        issues = planner.validate_plan(plan)

        assert len(issues) > 0


# ============================================================================
# TaskDecomposer Tests
# ============================================================================


class TestTaskDecomposer:
    """Tests for TaskDecomposer."""

    def test_initialization(self):
        """Basic initialization."""
        decomposer = TaskDecomposer()

        assert decomposer.max_depth == 3

    @pytest.mark.asyncio
    @patch("core.services.llm.get_llm_service", side_effect=ImportError)
    async def test_decompose_simple(self, mock_get):
        """Decompose without LLM."""
        decomposer = TaskDecomposer()
        decomposer._llm_service = None

        subtasks = await decomposer.decompose("Build app", min_subtasks=2)

        assert len(subtasks) >= 2

    @pytest.mark.asyncio
    async def test_decompose_respects_max(self):
        """Decomposition respects max_subtasks."""
        decomposer = TaskDecomposer()

        subtasks = await decomposer.decompose(
            "Complex task",
            min_subtasks=2,
            max_subtasks=3,
        )

        assert len(subtasks) <= 3

    @pytest.mark.asyncio
    async def test_subtask_has_id(self):
        """Subtasks have unique IDs."""
        decomposer = TaskDecomposer()

        subtasks = await decomposer.decompose("task")

        ids = [s.id for s in subtasks]
        assert len(ids) == len(set(ids))  # All unique

    @pytest.mark.asyncio
    async def test_subtask_has_effort(self):
        """Subtasks have effort estimates."""
        decomposer = TaskDecomposer()

        subtasks = await decomposer.decompose("task")

        assert all(0 <= s.estimated_effort <= 1 for s in subtasks)


# ============================================================================
# Integration Test
# ============================================================================


@pytest.mark.asyncio
@patch("core.services.llm.get_llm_service", side_effect=ImportError)
async def test_plan_execution_flow(mock_get):
    """Full planning workflow."""
    planner = TaskPlanner()

    # Create plan
    plan = await planner.create_plan("Deploy application")

    # Validate
    issues = planner.validate_plan(plan)
    assert len(issues) == 0

    # Execute steps in order
    completed = set()
    while not plan.is_complete:
        next_steps = plan.get_next_steps()
        if not next_steps:
            break

        for step in next_steps:
            step.status = StepStatus.COMPLETED
            completed.add(step.id)

    assert plan.is_complete
    assert plan.progress == 1.0
