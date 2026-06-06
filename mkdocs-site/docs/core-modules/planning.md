---
title: Planning
description: Intelligent task planning, decomposition, and budget-aware execution
---

## Overview

The `core/planning` module enables agents to handle complex goals by breaking them down into manageable subtasks. It supports **Budget-Aware Planning** to ensure efficient resource usage (steps, tokens, latency).

**Key Features**:

- **Hierarchical Planning**: Decomposes high-level goals into step-by-step plans
- **Budget Constraints**: Enforces limits on steps, tokens, and tool calls
- **Dynamic Adaptation**: Re-plans when steps fail or context changes
- **LLM & Heuristic Modes**: Supports both LLM-driven complex planning and fast heuristic fallbacks

## Architecture

The planning system consists of:

1. **TaskPlanner**: The main entry point for creating and managing plans.
2. **TaskDecomposer**: specialized component for breaking down complex tasks.
3. **PlanningBudget**: Value objects defining resource constraints.

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

## Best Practices

!!! tip "Set Realistic Budgets"
    Always provide a `PlanningBudget` for user-facing agents to prevent excessive costs. Start with conservative limits (e.g., 10 steps) and increase as needed.

!!! warning "Handle Failures"
    Plans can fail. Implement a retry loop that feeds the error back to the planner to generate a corrected plan.
