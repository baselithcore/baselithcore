---
title: Prioritization
description: Weighted task scoring, dependency tracking, and a priority queue for agent work scheduling
---

The `core/prioritization` module decides **which piece of work an agent should
pick up next**. A `TaskPrioritizer` scores each `Task` on five weighted factors
— urgency, importance, effort, deadline proximity, and how many other tasks it
unblocks — a `DependencyGraph` holds tasks back until their prerequisites
complete, and a heap-backed `PriorityQueue` hands out the highest-scoring
*ready* task. It is an in-process, synchronous scheduler for planner or
decomposer output; moving jobs between processes is the
[Task Queue](task-queue.md)'s job.

Nothing in the runtime enqueues into it implicitly: the orchestrator does not
consult a `PriorityQueue` on its own. You build one around the steps a
[planner](planning.md) produced (or any other list of `Task` objects) and
drive the loop yourself.

!!! note "Not to be confused with intent priority"
    Intent routing has its own, unrelated `priority`: an integer on each intent
    pattern that only orders **keyword matching** in the
    [Intent Classifier](orchestration.md#intent-classifier) (higher first). It
    is set through `register_intent(intent_name, patterns, priority=0,
    description="")` or via the `"name"`-keyed pattern dicts a plugin returns
    from [`AgentPlugin.get_intent_patterns()`](plugins.md#agentplugin)
    (`{"name": ..., "patterns": [...], "priority": ...}`). Neither path
    touches this module.

---

## Structure

```text
core/prioritization/
├── __init__.py   # public API: Task, TaskStatus, DependencyGraph,
│                 #             PriorityQueue, PriorityScore, TaskPrioritizer
├── models.py     # Task dataclass, TaskStatus enum, DependencyGraph
├── scorer.py     # TaskPrioritizer (weighted scoring) + PriorityScore breakdown
└── queue.py      # PriorityQueue — heap of READY tasks over a DependencyGraph
```

```python
from core.prioritization import (
    DependencyGraph,  # prerequisite tracking, cycle check, topological order
    PriorityQueue,    # enqueue / dequeue / complete / fail / reprioritize
    PriorityScore,    # total plus per-factor breakdown
    Task,             # unit of work carrying the priority factors
    TaskPrioritizer,  # weighted scorer, configured from PrioritizationConfig
    TaskStatus,       # PENDING | READY | IN_PROGRESS | COMPLETED | FAILED | BLOCKED
)
```

The scoring weights live in `core.config.prioritization.PrioritizationConfig`
(also exported from `core.config`); see [Configuration](#configuration).

---

## Tasks (`Task`, `TaskStatus`)

`Task` is a plain dataclass. Everything except `id` and `name` has a default,
so a bare `Task(id="x", name="x")` scores a neutral `0.45` under the default
weights.

| Field          | Type               | Default          | Role                                                       |
| -------------- | ------------------ | ---------------- | ---------------------------------------------------------- |
| `id`           | `str`              | —                | Unique key; referenced by other tasks' `dependencies`      |
| `name`         | `str`              | —                | Human-readable label                                       |
| `description`  | `str`              | `""`             | Free text                                                  |
| `status`       | `TaskStatus`       | `PENDING`        | Lifecycle state, driven by the queue                       |
| `urgency`      | `float`            | `0.5`            | 0.0–1.0, how soon it needs attention                       |
| `importance`   | `float`            | `0.5`            | 0.0–1.0, how critical it is                                |
| `effort`       | `float`            | `0.5`            | 0.0–1.0, **lower is cheaper** — quick wins score higher    |
| `dependencies` | `list[str]`        | `[]`             | Ids of tasks that must complete first                      |
| `created_at`   | `datetime`         | `datetime.now()` | Naive local timestamp                                      |
| `deadline`     | `datetime \| None` | `None`           | Naive local datetime (see the warning under Scoring)       |
| `tags`         | `list[str]`        | `[]`             | Free-form labels                                           |
| `metadata`     | `dict`             | `{}`             | Free-form payload                                          |

`task.is_ready(completed_tasks: set[str]) -> bool` is true when every id in
`dependencies` is in the set.

`TaskStatus` values: `PENDING` (added, not yet scheduled), `READY`
(dependencies satisfied, sitting in the heap), `BLOCKED` (waiting for
dependencies), `IN_PROGRESS` (handed out by `dequeue()`), `COMPLETED`,
`FAILED`.

---

## Scoring (`TaskPrioritizer`, `PriorityScore`)

`TaskPrioritizer.score(task, dependent_count=0, max_dependents=10)` returns a
`PriorityScore` whose `total` is a weighted sum clamped to 0.0–1.0, with one
`*_component` field per factor (`urgency_component`, `importance_component`,
`effort_component`, `deadline_component`, `dependency_component`):

| Factor       | Component input                                                                                | Weight (default)             |
| ------------ | ---------------------------------------------------------------------------------------------- | ---------------------------- |
| Urgency      | `task.urgency`                                                                                 | `weight_urgency` (0.25)      |
| Importance   | `task.importance`                                                                              | `weight_importance` (0.30)   |
| Effort       | `1.0 - task.effort`                                                                            | `weight_effort` (0.15)       |
| Deadline     | `0.5` with no deadline; `1.0` when overdue; `0.0` at 30 days out or more; linear in between    | `weight_deadline` (0.20)     |
| Dependencies | `min(1.0, dependent_count / max_dependents)`                                                   | `weight_dependencies` (0.10) |

```python
from datetime import datetime, timedelta
from core.prioritization import Task, TaskPrioritizer

prioritizer = TaskPrioritizer()  # weights from PrioritizationConfig (env or defaults)

task = Task(id="hotfix", name="Ship the hotfix", deadline=datetime.now() - timedelta(days=1))
score = prioritizer.score(task, dependent_count=3)
print(round(score.total, 2))        # 0.58
print(score.deadline_component)     # 0.2  -> overdue: 1.0 * weight_deadline
print(score.dependency_component)   # 0.03 -> 3/10 * weight_dependencies
```

Weights can be pinned per instance instead of read from the environment. A
`config=` argument wins outright (keyword weights are ignored when it is
given); without it, explicit keyword overrides take precedence and the
remaining weights come from the environment or the defaults:

```python
from core.config import PrioritizationConfig
from core.prioritization import TaskPrioritizer

TaskPrioritizer(config=PrioritizationConfig(weight_deadline=0.4))
TaskPrioritizer(weight_urgency=0.5)  # other four weights: env or defaults
```

!!! warning "Deadlines are naive local datetimes"
    The deadline score subtracts a naive `datetime.now()` from `task.deadline`.
    Pass a naive local datetime; a timezone-aware one raises `TypeError`
    (`can't subtract offset-naive and offset-aware datetimes`) inside
    `score()`.

---

## Dependency graph (`DependencyGraph`)

`PriorityQueue` owns one as `queue.graph`; it is also usable on its own for
ordering questions.

| Method                     | Returns        | Notes                                                                            |
| -------------------------- | -------------- | -------------------------------------------------------------------------------- |
| `add_task(task)`           | `None`         | Registers the task and its reverse edges. Cycles are **not** rejected — check `has_cycle()` |
| `remove_task(task_id)`     | `Task \| None` | Drops the task and its reverse edges                                             |
| `get_task(task_id)`        | `Task \| None` |                                                                                  |
| `get_ready_tasks()`        | `list[Task]`   | Tasks in `PENDING` or `READY` (not started) whose dependencies are all `COMPLETED` |
| `get_dependents(task_id)`  | `set[str]`     | Ids that list `task_id` as a dependency                                          |
| `mark_completed(task_id)`  | `list[str]`    | Sets `COMPLETED`; returns the `PENDING`/`BLOCKED` dependents now satisfiable     |
| `has_cycle()`              | `bool`         | Depth-first search over the dependency edges                                     |
| `topological_sort()`       | `list[str]`    | Dependencies before dependents                                                   |
| `len(graph)`               | `int`          | Number of tasks                                                                  |

```python
from core.prioritization import DependencyGraph, Task

graph = DependencyGraph()
graph.add_task(Task(id="a", name="fetch"))
graph.add_task(Task(id="b", name="index", dependencies=["a"]))

graph.has_cycle()                        # False
graph.topological_sort()                 # ['a', 'b']
[t.id for t in graph.get_ready_tasks()]  # ['a']
graph.mark_completed("a")                # ['b']
```

---

## Priority queue (`PriorityQueue`)

`PriorityQueue(prioritizer=None, config=None)` builds its own `TaskPrioritizer`
(from `config`, or the environment) when none is passed. All methods are
synchronous.

```python
from datetime import datetime, timedelta
from core.prioritization import PriorityQueue, Task

queue = PriorityQueue()

fetch = Task(id="fetch", name="Fetch source documents", urgency=0.8, importance=0.9, effort=0.3)
index = Task(
    id="index",
    name="Index the corpus",
    dependencies=["fetch"],
    importance=0.7,
    deadline=datetime.now() + timedelta(days=2),
)
report = Task(id="report", name="Write the summary", dependencies=["index"], effort=0.8)

for task in (fetch, index, report):
    score = queue.enqueue(task)                # -> PriorityScore
    print(task.id, task.status.value, round(score.total, 3))
# fetch ready 0.675
# index blocked 0.597
# report blocked 0.405

task = queue.dequeue()                         # highest-scoring READY task, now IN_PROGRESS
assert task is not None and task.id == "fetch"

newly_ready = queue.complete("fetch")          # -> [index], re-scored and pushed
queue.reprioritize("report", urgency=1.0)      # -> PriorityScore | None
queue.get_score("index")                       # -> PriorityScore | None
queue.dequeue().id                             # 'index'
len(queue)                                     # 1 -> report, still BLOCKED
```

Lifecycle:

- `enqueue(task)` adds the task to the graph, scores it, and pushes it on the
  heap as `READY` if its dependencies are already `COMPLETED`; otherwise it is
  parked as `BLOCKED`. Returns the `PriorityScore`.
- `dequeue()` pops until it finds a `READY` task, marks it `IN_PROGRESS`, and
  returns it; `None` when nothing is ready (blocked tasks do not count).
- `complete(task_id)` marks the task `COMPLETED`, flips each newly satisfiable
  dependent to `READY`, re-scores and pushes it, and returns those tasks.
- `fail(task_id)` only marks the task `FAILED`. Its dependents stay `BLOCKED`
  — nothing unblocks or fails them in cascade. To recover, enqueue a
  replacement `Task` with the **same `id`**: it is scored and pushed as
  `READY`, and `complete()` on it releases the original dependents.
- `reprioritize(task_id, urgency=None, importance=None)` clamps the new
  factors to 0.0–1.0, re-scores, and pushes a fresh heap entry when the task
  is `READY`; the superseded entry is discarded lazily by `dequeue()`, so
  raising *and* lowering a score both take effect on the next `dequeue()`.
  Called with no factor arguments it simply recomputes the score against the
  current graph.
- `get_score(task_id)` returns the last computed `PriorityScore`;
  `len(queue)` counts tasks in `PENDING`, `READY` or `BLOCKED`.
- `get_ready_tasks()` returns the queued tasks that are unblocked and not
  yet started (`PENDING` or `READY`) with their `PriorityScore`, highest
  first — a read-only preview of what `dequeue()` will hand out.

Two scoring details worth knowing:

- The dependency component counts the dependents known **when the score is
  computed**. A task enqueued before its dependents scores without them;
  `reprioritize(task_id)` refreshes it once the batch is in.
- `reprioritize()` pushes a second heap entry rather than rewriting the heap;
  `dequeue()` skips any entry whose priority no longer matches the task's
  current score, so the heap may hold stale entries but never serves them.

---

## Configuration

`PrioritizationConfig` (`core/config/prioritization.py`) is a pydantic-settings
model read from `PRIORITIZATION_`-prefixed environment variables. The defaults
sum to `1.0`, which keeps `total` inside 0.0–1.0 without clamping; overrides
that sum higher are clamped at `1.0`.

| Setting (`PrioritizationConfig`) | Environment variable                  | Default | Weights                                       |
| -------------------------------- | ------------------------------------- | ------- | --------------------------------------------- |
| `weight_urgency`                 | `PRIORITIZATION_WEIGHT_URGENCY`       | `0.25`  | `Task.urgency`                                |
| `weight_importance`              | `PRIORITIZATION_WEIGHT_IMPORTANCE`    | `0.30`  | `Task.importance`                             |
| `weight_effort`                  | `PRIORITIZATION_WEIGHT_EFFORT`        | `0.15`  | `1.0 - Task.effort`                           |
| `weight_deadline`                | `PRIORITIZATION_WEIGHT_DEADLINE`      | `0.20`  | Deadline proximity                            |
| `weight_dependencies`            | `PRIORITIZATION_WEIGHT_DEPENDENCIES`  | `0.10`  | Dependents, normalised by `max_dependents`    |

```env
PRIORITIZATION_WEIGHT_URGENCY=0.25
PRIORITIZATION_WEIGHT_IMPORTANCE=0.30
PRIORITIZATION_WEIGHT_EFFORT=0.15
PRIORITIZATION_WEIGHT_DEADLINE=0.20
PRIORITIZATION_WEIGHT_DEPENDENCIES=0.10
```

Each field also declares the bare alias (`WEIGHT_URGENCY`, …), which
pydantic-settings reads as well; prefer the prefixed names so a value cannot
collide with another component's setting. There is no cached
`get_prioritization_config()` accessor — `TaskPrioritizer()` instantiates the
model itself, or pass one in via `config=`.

---

## The boundary

`core/prioritization` is domain-agnostic: it knows nothing about tenants,
SLAs, cost, or what a task does. Anything that decides *what the factors
should be* — mapping a customer tier to `importance`, an SLA clock to
`deadline`, a [`SubTask.estimated_effort`](planning.md#task-decomposer) to
`effort` — belongs in the plugin or service that builds the `Task`, not in
this package. A custom scorer stays in core only if it is generic (subclass
`TaskPrioritizer` and override `score()`); shared, per-process weights go
through `PrioritizationConfig`.

Related: [Planning](planning.md) for producing the steps,
[Task Queue](task-queue.md) for executing them across workers,
[Orchestration](orchestration.md) for the loop that consumes them, and
[Agentic Patterns](../architecture/agentic-patterns.md) for how the runtime
primitives fit together.
