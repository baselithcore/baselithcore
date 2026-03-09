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
    print(f"{step.id}: {step.instruction}")
```

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

# Plan metadata includes budget info
print(f"Budget remaining: {plan.metadata.get('budget_remaining')}")
```

## Planning Budget

The `PlanningBudget` class controls resource consumption.

```python
@dataclass
class PlanningBudget:
    max_steps: int = 10
    max_estimated_tokens: int = 10000
    max_tool_calls: int = 20
    max_latency_ms: int = 30000
```

| Constraint             | Description                            | Default |
| ---------------------- | -------------------------------------- | ------- |
| `max_steps`            | Maximum number of steps in the plan    | 10      |
| `max_estimated_tokens` | Token limit for planning and execution | 10k     |
| `max_tool_calls`       | Total allowed tool invocations         | 20      |
| `max_latency_ms`       | Maximum execution time in milliseconds | 30s     |

## Task Decomposer

For very complex tasks, `TaskDecomposer` can create a dependency graph of subtasks.

```python
from core.planning import TaskDecomposer

decomposer = TaskDecomposer(llm_service=llm_service)

subtasks = await decomposer.decompose(
    "Build a full-stack web app with auth and database"
)

# Returns a list of independent and dependent tasks
# e.g., "Design Schema" -> "Setup DB" -> "Implement API"
```

## Best Practices

!!! tip "Set Realistic Budgets"
    Always provide a `PlanningBudget` for user-facing agents to prevent excessive costs. Start with conservative limits (e.g., 10 steps) and increase as needed.

!!! warning "Handle Failures"
    Plans can fail. Implement a retry loop that feeds the error back to the planner to generate a corrected plan.
