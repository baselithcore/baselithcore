# Goals Plugin

## Overview

The `goals` plugin provides an advanced, stateful tracking mechanism for long-term agent objectives within the BaselithCore framework. It is designed to act as a resilient layer that monitors how well agents are progressing toward multi-step milestones.

## Why is it a Plugin?

While every agentic system executes tasks, the concept of **stateful, long-term goals with success criteria** is an advanced feature that not all applications require. Moving this to a plugin allows the core framework to remain lightweight, while enabling complex agents (e.g., autonomous researchers, long-running data analysts) to track progress across multiple sessions.

## Core Components

- `GoalTracker`: The central class that manages a registry of active, completed, and failed goals.
- `Goal`: A dataclass representing an individual objective, complete with progress tracking (0.0 to 1.0), success criteria, and completion timestamps.
- `GoalStatus`: An enumeration defining the lifecycle of a goal (`NOT_STARTED`, `IN_PROGRESS`, `COMPLETED`, `FAILED`, `ABANDONED`).

## Usage

To use the Goals plugin in your agent workflow:

1. **Enable the plugin** in `configs/plugins.yaml`:

   ```yaml
   goals:
     enabled: true
   ```

2. **Register a Goal**:

   ```python
   from plugins.goals.tracker import Goal, GoalTracker

   tracker = GoalTracker()
   my_goal = Goal(
       id="goal-001",
       title="Analyze Q3 Financials",
       success_criteria=["Data extracted", "Report generated"]
   )
   
   await tracker.add(my_goal)
   ```

3. **Update Progress**:

   ```python
   await tracker.update_progress("goal-001", 0.5) # 50% complete
   ```

4. **Complete or Fail**:

   ```python
   await tracker.complete("goal-001")
   # or
   await tracker.fail("goal-001", reason="Data source unreachable")
   ```

## Development

To extend the `goals` plugin, you can integrate it directly with the `memory` module to persist active goals across agent restarts, ensuring long-running tasks survive system reboots.
