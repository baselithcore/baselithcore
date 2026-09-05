---
title: Planning
description: Intelligent task planning, decomposition, and budget-aware execution
---

## Overview

The `core/planning` module enables agents to handle complex goals by breaking them down into manageable subtasks. It supports **Budget-Aware Planning** to ensure efficient resource usage (steps, tokens, latency).

**Key Features**:

- **Hierarchical Planning**: Decomposes high-level goals into step-by-step plans
- **Budget Constraints**: Enforces limits on steps, tokens, and tool calls
- **Dependency-Aware Execution**: `Plan.get_next_steps()` yields the pending steps whose dependencies have completed
- **LLM & Heuristic Modes**: Supports both LLM-driven complex planning and fast heuristic fallbacks

## Architecture

The planning system consists of:

1. **TaskPlanner**: The main entry point — `create_plan()` builds a `Plan` and `validate_plan()` checks it for unknown dependencies or an empty step list. There is no re-plan API.
2. **TaskDecomposer**: specialized component for breaking down complex tasks.
3. **PlanningBudget**: Value objects defining resource constraints.
4. **plan_to_workflow**: Adapter (`core/planning/adapter.py`) that turns a `Plan` into a `WorkflowDefinition` for the workflow engine.

## Usage

### Basic Planning

```python
from core.planning import TaskPlanner

planner = TaskPlanner(llm_service=llm_service)

# Create a plan for a goal
plan = await planner.create_plan("Research and summarize global warming trends")

for step in plan.steps:
    print(f"{step.id}: {step.description}")
```

Each `PlanStep` exposes `id`, `description`, `action`, `parameters`,
`dependencies`, `status`, and `result`.

### Budget-Aware Planning

To prevent runaway costs or infinite loops, use `PlanningBudget`:

```python
from core.planning import TaskPlanner, PlanningBudget

# Define strict constraints
budget = PlanningBudget(
    max_steps=5,              # Max plan steps
    max_estimated_tokens=2000,# Token budget
    max_tool_calls=10,        # Max total tool executions
    max_latency_ms=30000      # 30s timeout
)

planner = TaskPlanner()
plan = await planner.create_plan(
    "Analyze large dataset",
    budget=budget
)

# When a budget is supplied, create_plan records it under metadata["budget"]
print(plan.metadata.get("budget"))
# {'max_steps': 5, 'max_tokens': 2000, 'max_tool_calls': 10, 'max_latency_ms': 30000}
```

!!! note "Effective step cap"
    `create_plan` clamps `max_steps` to `min(max_steps, budget.max_steps)`
    before planning, so the budget always wins when it is stricter.

### Plan → Workflow

`plan_to_workflow` converts a `Plan` into a validated `WorkflowDefinition` so
the [workflow engine](workflows.md) can execute it (`name` defaults to
`plan.goal`):

```python
from core.planning import TaskPlanner, plan_to_workflow

planner = TaskPlanner(llm_service=llm_service)
plan = await planner.create_plan("Research and summarize global warming trends")

workflow = plan_to_workflow(plan, name="research", default_agent_id="researcher")
```

- A `START` node is created, then one `WorkflowNode` per step (id
  `step_<step.id>`, label = the step description, `config` carrying
  `plan_step_id`, `action` and the step `parameters`).
- The node type comes from `step.action` via `ACTION_MAP`: `analyze` /
  `execute` / `validate` → `AGENT`, `transform` → `TRANSFORM`, `check` /
  `condition` → `CONDITION`, `tool` → `TOOL`, `human` → `HUMAN`,
  `parallel` → `PARALLEL`; anything else defaults to `AGENT`.
- `AGENT` nodes take `parameters["agent_id"]`, falling back to
  `default_agent_id` (`"planner-agent"`); `TOOL` nodes read
  `parameters["tool_id"]` and `CONDITION` nodes `parameters["expression"]`.
- Edges follow `dependencies`: steps with none hang off `START`, an unknown
  dependency id is logged (the step attaches to `START` if none resolved),
  and steps with no successors are wired to `END`.
- `workflow.metadata["source"]` is `"plan_adapter"` and the plan's `metadata`
  (including any `budget`) is copied to `metadata["plan"]`. Validation
  issues are logged as warnings, not raised.

## Planning Budget

The `PlanningBudget` class controls resource consumption.

```python
@dataclass
class PlanningBudget:
    max_steps: int = 10
    max_estimated_tokens: int = 10000
    max_tool_calls: int = 20
    max_latency_ms: int = 30000
    cost_per_step: float = 100.0
    cost_per_tool_call: float = 50.0
```

| Constraint             | Description                            | Default |
| ---------------------- | -------------------------------------- | ------- |
| `max_steps`            | Maximum number of steps in the plan    | 10      |
| `max_estimated_tokens` | Token limit for planning and execution | 10000   |
| `max_tool_calls`       | Total allowed tool invocations         | 20      |
| `max_latency_ms`       | Maximum execution time in milliseconds | 30000   |
| `cost_per_step`        | Estimated token cost per step          | 100.0   |
| `cost_per_tool_call`   | Estimated token cost per tool call     | 50.0    |

`PlanningBudget` also offers `remaining_budget(...)` (returns
`BudgetRemaining`) and `is_exhausted(...)` to track consumption during
execution.

## Task Decomposer

For very complex tasks, `TaskDecomposer` breaks a task into a flat list of
`SubTask` objects (it does **not** build a dependency graph).

```python
from core.planning import TaskDecomposer

decomposer = TaskDecomposer(llm_service=llm_service)

subtasks = await decomposer.decompose(
    "Build a full-stack web app with auth and database",
    min_subtasks=2,
    max_subtasks=5,
)

for sub in subtasks:
    print(sub.id, sub.title, sub.description, sub.estimated_effort)
```

Each `SubTask` carries `id`, `title`, `description`, `parent_id`,
`estimated_effort` (0.0–1.0), and `tags`. There is no inter-subtask
dependency field; ordering/dependencies are the planner's concern, not the
decomposer's.

## Plan-Approve Gate

`TaskPlanner` produces a reviewable `Plan` and `PlanCostEstimate` prices it,
but nothing composed them into *emit plan → block for sign-off → execute*.
`approve_plan` (`core/planning/approval.py`) is that composition: under an
`AutonomyPolicy` whose level requires approval for **mutating** work (plan
execution changes state by definition), the rendered plan goes to the
`HumanIntervention` channel and execution is blocked until an explicit yes.

```python
from core.human.interaction import HumanIntervention
from core.orchestration.autonomy import AutonomyLevel, AutonomyPolicy
from core.planning import PlanRejectedError, TaskPlanner, approve_plan

planner = TaskPlanner(llm_service=llm_service)
plan = await planner.create_plan("Migrate the billing tables to the new schema")

policy = AutonomyPolicy(level=AutonomyLevel.SUPERVISED)
human = HumanIntervention(callback=my_ui_callback)

try:
    await approve_plan(plan, policy=policy, human=human, timeout=300)
except PlanRejectedError:
    return  # denied, timed out, or no channel — do not execute
# ... proceed with plan execution
```

Behavior matrix:

| Situation | Outcome |
| --------- | ------- |
| `policy is None`, or `policy.requires_approval("mutating")` is false (e.g. `FULLY_AUTONOMOUS`) | Pass-through — returns `True` without contacting the human |
| Gate applies, `human is None` | Raises `PlanRejectedError` (**fail closed**) |
| Gate applies, human approves | Returns `True` |
| Gate applies, human denies or the wait times out | Raises `PlanRejectedError` |

The rendered review block comes from `render_plan_for_review(plan, estimate)`:
the goal, numbered steps with `[action]` tags and `(after step_a, step_b)`
dependency notes, and — when a `PlanCostEstimate` is passed via `estimate=` —
an `**Estimated cost:**` line (`~tokens, tool call(s), ~latency ms`). The
approval request also carries `{"goal", "steps", "estimated_tokens"}` as
structured context for the reviewing surface.

`PlanRejectedError` subclasses `PermissionError`, so existing autonomy-denial
handling catches it. See
[Orchestration → `AutonomyPolicy`](orchestration.md#autonomypolicy-three-tier-spectrum)
for the approval matrix and [Human-in-the-Loop](human.md) for the channel.

## Best Practices

!!! tip "Set Realistic Budgets"
    Always provide a `PlanningBudget` for user-facing agents to prevent excessive costs. Start with conservative limits (e.g., 10 steps) and increase as needed.

!!! warning "Handle Failures"
    Plans can fail, and `TaskPlanner` does not re-plan on its own — `create_plan` is its only entry point. To recover, mark the failed step `StepStatus.FAILED` (`core.planning.planner`) so `get_next_steps()` stops scheduling its dependants, then call `create_plan` again with the error described in `context`.
