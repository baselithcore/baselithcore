"""
High-Level Task Planning Module.

Coordinates the creation and management of execution plans from abstract
goals. Integrates with LLM services for intelligent reasoning and supports
dependency-aware step ordering.
"""

from core.observability.logging import get_logger
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Optional
from enum import Enum

if TYPE_CHECKING:
    from .budget import PlanningBudget


logger = get_logger(__name__)


class StepStatus(Enum):
    """
    Lifecycle states for an individual plan step.
    """

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class PlanStep:
    """
    An atomic action within an execution plan.

    Attributes:
        id: Unique identifier for the step.
        description: Human-readable explanation of the task.
        action: The semantic operation type (e.g., 'analyze', 'execute').
        parameters: Configuration payload for the action.
        dependencies: IDs of steps that must complete before this one.
        status: Current execution state.
        result: Output or artifact produced by the step.
    """

    id: str
    description: str
    action: str  # Action identifier
    parameters: Dict = field(default_factory=dict)
    dependencies: List[str] = field(default_factory=list)
    status: StepStatus = StepStatus.PENDING
    result: Optional[str] = None

    def is_ready(self, completed: set) -> bool:
        """Check if step dependencies are satisfied."""
        return all(dep in completed for dep in self.dependencies)


@dataclass
class Plan:
    """An execution plan with ordered steps."""

    goal: str
    steps: List[PlanStep] = field(default_factory=list)
    metadata: Dict = field(default_factory=dict)

    @property
    def is_complete(self) -> bool:
        """Check if all steps are completed."""
        return all(s.status == StepStatus.COMPLETED for s in self.steps)

    @property
    def progress(self) -> float:
        """Calculate completion percentage."""
        if not self.steps:
            return 0.0
        completed = sum(1 for s in self.steps if s.status == StepStatus.COMPLETED)
        return completed / len(self.steps)

    def get_next_steps(self) -> List[PlanStep]:
        """Get steps that are ready to execute."""
        completed = {s.id for s in self.steps if s.status == StepStatus.COMPLETED}
        return [
            s
            for s in self.steps
            if s.status == StepStatus.PENDING and s.is_ready(completed)
        ]


class TaskPlanner:
    """
    Creates execution plans from goals.

    Features:
    - Goal decomposition
    - Step ordering based on dependencies
    - Plan validation
    """

    def __init__(self, llm_service=None):
        """
        Initialize planner.

        Args:
            llm_service: Optional LLM for intelligent planning
        """
        self._llm_service = llm_service

    @property
    def llm_service(self):
        """Lazy load LLM service."""
        if self._llm_service is None:
            try:
                from core.services.llm import get_llm_service

                self._llm_service = get_llm_service()
            except ImportError:
                pass
        return self._llm_service

    async def create_plan(
        self,
        goal: str,
        context: Optional[Dict] = None,
        max_steps: int = 10,
        budget: Optional["PlanningBudget"] = None,  # NEW - backward compatible
    ) -> Plan:
        """
        Create an execution plan for a goal.

        Args:
            goal: The high-level goal to achieve
            context: Optional context information
            max_steps: Maximum number of steps
            budget: Optional budget constraints (tokens, latency, tool calls)

        Returns:
            Plan with ordered steps
        """
        # Apply budget constraints if provided
        effective_max_steps = max_steps
        if budget:
            effective_max_steps = min(max_steps, budget.max_steps)

        # Try LLM-based planning first
        if self.llm_service:
            plan = await self._create_with_llm(
                goal, context, effective_max_steps, budget
            )
        else:
            # Fallback to simple planning
            plan = self._create_simple(goal, effective_max_steps)

        # Attach budget metadata if provided
        if budget:
            plan.metadata["budget"] = {
                "max_steps": budget.max_steps,
                "max_tokens": budget.max_estimated_tokens,
                "max_tool_calls": budget.max_tool_calls,
                "max_latency_ms": budget.max_latency_ms,
            }

        return plan

    async def _create_with_llm(
        self,
        goal: str,
        context: Optional[Dict],
        max_steps: int,
        budget: Optional["PlanningBudget"] = None,
    ) -> Plan:
        """Create plan using LLM."""
        ctx_str = str(context) if context else "None"

        prompt = f"""Create an execution plan for this goal:
Goal: {goal}
Context: {ctx_str}

Create up to {max_steps} steps. For each step provide:
- ID (step1, step2, etc.)
- Description
- Action (analyze, execute, validate, etc.)
- Dependencies (IDs of prerequisite steps)

Format:
STEP: <id>
DESCRIPTION: <what to do>
ACTION: <action type>
DEPENDS: <comma-separated step IDs or "none">
---

Constraints:
1. Maximum Steps: {max_steps}
2. Token Budget: {budget.max_estimated_tokens if budget else "Unlimited"}
3. Tool Call Limit: {budget.max_tool_calls if budget else "Unlimited"}
4. Efficiency: Optimize for minimal steps and tool usage.
"""

        try:
            result = await self.llm_service.generate_response(prompt)
            steps = self._parse_steps(result)
            return Plan(goal=goal, steps=steps, metadata=context or {})
        except Exception as e:
            logger.warning(f"LLM planning failed: {e}")
            return self._create_simple(goal, max_steps)

    def _create_simple(self, goal: str, max_steps: int) -> Plan:
        """Create simple default plan."""
        steps = [
            PlanStep(
                id="step1",
                description=f"Analyze goal: {goal}",
                action="analyze",
            ),
            PlanStep(
                id="step2",
                description="Execute main task",
                action="execute",
                dependencies=["step1"],
            ),
            PlanStep(
                id="step3",
                description="Validate results",
                action="validate",
                dependencies=["step2"],
            ),
        ]
        return Plan(goal=goal, steps=steps[:max_steps])

    def _parse_steps(self, text: str) -> List[PlanStep]:
        """Parse LLM output into PlanStep objects."""
        steps = []

        for block in text.split("---"):
            if "STEP:" not in block:
                continue

            try:
                step_id = ""
                description = ""
                action = ""
                deps = []

                for line in block.strip().split("\n"):
                    if line.startswith("STEP:"):
                        step_id = line.replace("STEP:", "").strip()
                    elif line.startswith("DESCRIPTION:"):
                        description = line.replace("DESCRIPTION:", "").strip()
                    elif line.startswith("ACTION:"):
                        action = line.replace("ACTION:", "").strip()
                    elif line.startswith("DEPENDS:"):
                        dep_str = line.replace("DEPENDS:", "").strip()
                        if dep_str.lower() != "none":
                            deps = [d.strip() for d in dep_str.split(",")]

                if step_id and description:
                    steps.append(
                        PlanStep(
                            id=step_id,
                            description=description,
                            action=action or "execute",
                            dependencies=deps,
                        )
                    )
            except Exception as e:
                logger.warning(f"Failed to parse plan step: {e}")
                continue

        return steps

    def validate_plan(self, plan: Plan) -> List[str]:
        """Validate plan for issues."""
        issues = []

        step_ids = {s.id for s in plan.steps}

        for step in plan.steps:
            for dep in step.dependencies:
                if dep not in step_ids:
                    issues.append(f"Step {step.id} has unknown dependency: {dep}")

        if not plan.steps:
            issues.append("Plan has no steps")

        return issues
