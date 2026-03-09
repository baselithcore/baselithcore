# Goals Plugin

The `goals` plugin provides a mechanism for agents to track, monitor, and manage long-term objectives.

## Overview

Complex autonomous agents often need to maintain state about their progress toward a high-level objective that spans multiple steps or sessions. The `goals` plugin provides a standardized way to:

- **Define a `Goal`** with specific success criteria.
- **Track progress** (0% to 100%).
- **Handle success**, failure, and abandonment states.

!!! note "Core vs Plugin"
    This functionality is implemented as a plugin to ensure the core framework remains lightweight and agnostic. It serves as an opt-in component for agents that specifically require stateful goal tracking, adhering to the framework's modular architecture.

## Key Concepts

### Goal

The fundamental unit of tracking. A goal consists of:

- **ID/Title**: Unique identifier and human-readable name.
- **Status**: `NOT_STARTED`, `IN_PROGRESS`, `COMPLETED`, `FAILED`, `ABANDONED`.
- **Progress**: A float value between 0.0 and 1.0.
- **Success Criteria**: A list of conditions that must be met.
- **Metadata**: Arbitrary dictionary for agent-specific context.

### GoalTracker

The manager class that handles:

- **Registering new goals**.
- **Updating progress safely**.
- **Validating criteria**.
- **Persisting goal state** (if persistence is enabled).

## Usage

### Defining a Goal

```python
from plugins.goals import Goal, GoalTracker

# Create a tracker
tracker = GoalTracker()

# Define a new goal
my_goal = Goal(
    id="build_website_v1",
    title="Build Personal Website",
    description="Create a portfolio site using Next.js",
    success_criteria=[
        "Homepage created",
        "About page created",
        "Deployed to Vercel"
    ]
)

# Register it
await tracker.add(my_goal)
```

### Updating Progress

```python
# Update progress to 50%
await tracker.update_progress("build_website_v1", 0.5)

# Mark specific criteria as met
my_goal.criteria_met["Homepage created"] = True
```

### Completing a Goal

```python
# Validate if all criteria are met
if tracker.validate_criteria("build_website_v1"):
    await tracker.complete("build_website_v1")
else:
    print("Cannot complete: Criteria not met.")
```

## Best Practices

- **Use for long-running tasks**: If an action is atomic and instantaneous, you don't need a Goal.
- **Granularity**: Break down massive goals into sub-goals if possible, though the current plugin focuses on flat goal structures.
- **Persistence**: Ensure your agent saves the state of the `GoalTracker` if it needs to survive restarts (the plugin provides the structure, but persistence depends on your agent's memory implementation).
