# Workflow Engine

The `core/workflows/` module provides a **graph-based workflow engine** for building, serializing, and executing baselith-core pipelines. Workflows are defined as directed graphs of typed nodes, with support for branching, parallelism, loops, and human-in-the-loop steps.

## Module Structure

```txt
core/workflows/
├── builder.py        # WorkflowDefinition, WorkflowNode, WorkflowEdge
├── executor.py       # WorkflowExecutor — async graph execution
├── node_handlers.py  # Default handlers per NodeType
├── conditions.py     # Safe AST condition evaluator
├── adapters.py       # CrewNodeAdapter / ColonyNodeAdapter — multi-agent primitives behind the AGENT-node contract
├── schedule.py       # WorkflowScheduler — in-process cron scheduling
└── flow_handler.py   # WorkflowFlowHandler — orchestrator bridge
```

---

## WorkflowBuilder

Define workflows programmatically as directed graphs.

There are two ways to assemble a workflow. The low-level
`WorkflowDefinition` works with explicit `WorkflowNode` / `WorkflowEdge`
objects; the fluent `WorkflowBuilder` auto-generates node ids and wires each
node to the previous one.

### Low-level: `WorkflowDefinition`

`add_node(node)` and `add_edge(edge)` both return `None` and mutate the
definition in place. `add_edge` takes a single `WorkflowEdge`:

```python
from core.workflows.builder import (
    WorkflowDefinition,
    WorkflowNode,
    WorkflowEdge,
    NodeType,
)

wf = WorkflowDefinition(name="research-pipeline")

# Add nodes (add_node returns None)
wf.add_node(WorkflowNode(id="start", type=NodeType.START, label="Start"))
wf.add_node(WorkflowNode(
    id="search",
    type=NodeType.AGENT,
    label="Web Search",
    agent_id="researcher-agent",
    config={"max_results": 10},
    timeout=30.0,
))
wf.add_node(WorkflowNode(
    id="report",
    type=NodeType.AGENT,
    label="Report Writer",
    agent_id="writer-agent",
))
wf.add_node(WorkflowNode(id="end", type=NodeType.END, label="End"))

# Connect nodes — one WorkflowEdge per call
wf.add_edge(WorkflowEdge(id="e1", source_id="start", target_id="search"))
wf.add_edge(WorkflowEdge(id="e2", source_id="search", target_id="report"))
wf.add_edge(WorkflowEdge(id="e3", source_id="report", target_id="end"))

# Serialize to JSON (for storage or the Flow Designer UI)
json_str = wf.to_json()

# Deserialize
wf2 = WorkflowDefinition.from_json(json_str)
```

### Fluent: `WorkflowBuilder`

```python
from core.workflows.builder import WorkflowBuilder

wf = (
    WorkflowBuilder(name="research-pipeline")
    .start()
    .agent("Web Search", agent_id="researcher-agent", max_results=10)
    .agent("Report Writer", agent_id="writer-agent")
    .end()
    .build()
)
```

Each builder method (`start`, `end`, `agent`, `tool`, `condition`,
`transform`, `parallel`, `merge`, `human`, `subgraph`) returns the builder for
chaining and auto-connects from the previously added node. `build()` returns
the finished `WorkflowDefinition`. `.human(label="Human Gate", **config)` adds
a [durable approval gate](#human-approval-gates-human-nodes); its
`config["category"]` (default `"human_gate"`) is the approval category shown
to the reviewer.

---

## Node Types

| Type        | Description                | Key Fields               |
| ----------- | -------------------------- | ------------------------ |
| `START`     | Entry point                | —                        |
| `END`       | Exit point                 | —                        |
| `AGENT`     | AI agent execution         | `agent_id`, `config`     |
| `TOOL`      | Tool invocation            | `tool_id`, `config`      |
| `CONDITION` | Conditional branch         | `condition_expression`   |
| `PARALLEL`  | Fan-out parallel execution | —                        |
| `MERGE`     | Fan-in merge branches      | —                        |
| `LOOP`      | Unsupported — fails closed (model cycles with `CONDITION` edges) | — |
| `HUMAN`     | Durable human-approval gate ([details](#human-approval-gates-human-nodes)) | `config["category"]` |
| `TRANSFORM` | Data transformation        | `config["transform"]` callable |
| `SUBGRAPH`  | Nested workflow composition | `config["workflow"]` (`WorkflowDefinition` or its `to_dict()`) |

!!! note "Node fields"
    `condition_expression` is a top-level field on `WorkflowNode` (not under
    `config`). The default `TRANSFORM` handler looks up a callable at
    `config["transform"]` and applies it to the upstream output, passing the
    input through unchanged if none is set. `LOOP` **fails closed** with a
    clear error: cycles are modeled with a `CONDITION` edge pointing back to
    an earlier node (bounded by `max_steps`) — a distinct loop construct
    would duplicate that, and silently passing traffic through an
    unimplemented node was the same hole `HUMAN` nodes used to have.
    Registering a custom `LOOP` handler via `register_handler` overrides the
    fail-closed default.

### Condition Expressions

Condition nodes use a **safe AST evaluator** (`_safe_condition` in
`executor.py`) — no `eval()`, no code injection risk. The expression is
evaluated against the workflow context variables, and the boolean result
selects the outgoing edge whose `condition_label` is `"true"` or `"false"`:

```python
wf.add_node(WorkflowNode(
    id="check-confidence",
    type=NodeType.CONDITION,
    label="High Confidence?",
    condition_expression="score > 0.85 and status == 'ok'",
    # Variables 'score' and 'status' come from the workflow context
))
```

Supported AST constructs:

- **Comparisons**: `==`, `!=`, `<`, `>`, `<=`, `>=`, `in`, `not in`, `is`, `is not`
- **Boolean / unary**: `and`, `or`, `not`, unary `-`
- **Arithmetic**: `+`, `-`, `*` (the only binary ops in `_SAFE_OPS`)
- **Attribute access** (private/dunder attributes are rejected)
- **Subscript access** (`data["key"]`, `items[0]`)
- **Ternary**: `a if cond else b`
- **Whitelisted calls only**: `len`, `str`, `int`, `float`, `bool` — any other call raises `ValueError`

Any unsupported node or an undefined variable raises `ValueError`.

---

## WorkflowExecutor

Executes a `WorkflowDefinition` asynchronously, step by step.

Default handlers cover every node type (bodies in
`core/workflows/node_handlers.py`); `LOOP`'s default fails closed (see the
node-fields note above). `register_handler` is a regular method (not a
decorator). Handlers receive `(node, context)` and may be sync or async —
registering one overrides the default for that type.

`AGENT` nodes resolve `config["agent"]` (any object with `async run(prompt)`,
e.g. [`core.agent.Agent`](agent.md)) or their `agent_id` in the executor's
`agents` registry; the prompt is `config["prompt"]` with `{input}` replaced
by the upstream output, or the upstream output itself. `TOOL` nodes resolve
`config["fn"]` or their `tool_id` in the `tools` registry and are called with
the upstream output:

```python
from core.agent import Agent
from core.workflows.executor import WorkflowExecutor

executor = WorkflowExecutor(
    agents={"analysis-agent": Agent(system_prompt="You analyze data.")},
    tools={"fetch": fetch_document},
)

# Execute (the workflow is validated first; it must contain a START node)
result = await executor.execute(
    workflow=wf,
    initial_input={"query": "research topic"},
)
print(result.status)        # ExecutionStatus.COMPLETED
print(result.output)        # Output of the last executed node
print(result.node_results)  # Dict[node_id, NodeResult]
print(result.duration_ms)   # Total time in milliseconds (property)
```

`WorkflowResult` fields: `workflow_id`, `status`, `output` (last node's
output), `error`, `node_results` (`Dict[str, NodeResult]`), `started_at`,
`completed_at`, and the computed `duration_ms` property. Each `NodeResult`
carries `node_id`, `status`, `output`, `error`, and `duration_ms`.

### Crews as agent nodes — `CrewNodeAdapter`

The AGENT-node contract is `async run(prompt)` returning an object with
`.output`. A [`Crew`](agent.md#multi-agent-crews-crew-task) speaks a different
dialect — `async run(inputs)` returning a `CrewResult` — so
`CrewNodeAdapter` (`core/workflows/adapters.py`, exported from
`core.workflows`) bridges it onto the contract: the node's prompt is bound
under `input_key` (default `"input"`, referenced by task descriptions as
`{input}`), and `CrewResult.final` becomes the node output. A whole crew then
composes into a graph — and inherits durable execution — as one node:

```python
from core.agent import Agent, Crew, Task
from core.workflows import CrewNodeAdapter, WorkflowExecutor
from core.workflows.builder import WorkflowBuilder

crew = Crew(
    agents=[Agent(system_prompt="You are a meticulous researcher.")],
    tasks=[Task("Research {input} and list the key facts.")],
)

executor = WorkflowExecutor(agents={"research-crew": CrewNodeAdapter(crew)})
wf = (
    WorkflowBuilder(name="crew-pipeline")
    .start()
    .agent("Research", agent_id="research-crew")
    .end()
    .build()
)
result = await executor.execute(wf, initial_input="vector databases")
```

### Colony tasks as agent nodes — `ColonyNodeAdapter`

[`Colony`](swarm.md#colony) speaks yet another dialect: a swarm `Task`
auctioned to the best-bidding agent and executed through
`execute_batch(tasks, execute_fn)`. `ColonyNodeAdapter`
(`core/workflows/adapters.py`, exported from `core.workflows`) bridges it onto
the AGENT-node contract: the node's prompt becomes a `Task`
(`description=prompt`, filtered by the adapter's `required_capabilities`), the
colony's auction picks the executing agent, and the caller-supplied
`execute_fn` — the same async `(task, agent) -> result` callback contract as
`execute_batch` — performs the work. The single task's output becomes the node
output; a failed **or unassigned** task (no agent won the auction) raises, so
the graph's per-node retry/error semantics apply:

```python
from core.swarm import AgentProfile, Capability, Colony, Task
from core.workflows import ColonyNodeAdapter, WorkflowExecutor
from core.workflows.builder import WorkflowBuilder

colony = Colony()
colony.register_agent(AgentProfile(
    id="analyst", name="Analyst", capabilities=[Capability("analyze")],
))

async def run(task: Task, agent: AgentProfile) -> str:
    return f"{agent.name} handled: {task.description}"

executor = WorkflowExecutor(agents={
    "swarm-analysis": ColonyNodeAdapter(
        colony, run, required_capabilities=["analyze"]
    ),
})
wf = (
    WorkflowBuilder(name="swarm-pipeline")
    .start()
    .agent("Analyze", agent_id="swarm-analysis")
    .end()
    .build()
)
result = await executor.execute(wf, initial_input="Q3 error-rate spike")
```

With `CrewNodeAdapter` and `ColonyNodeAdapter` together, both multi-agent
primitives compose into graphs (and inherit durable execution): a **crew**
node when the collaboration is a fixed task pipeline, a **colony** node when
the executing agent should be chosen by the auction per task.

### Execution Features

| Feature                 | Description                                                          |
| ----------------------- | -------------------------------------------------------------------- |
| **Timeouts**            | Per-node `timeout` field (`asyncio.wait_for`); a timeout fails the node |
| **Per-node retry**      | `retries` + `retry_backoff` fields: extra attempts with exponential backoff; timeouts are never retried |
| **Parallel + fan-in**   | `PARALLEL` nodes fan out to all outgoing edges with `asyncio.gather`; branches halt at their convergence `MERGE` node, which then executes **once** with the list of branch outputs and the chain continues past it. Branches must converge on the same `MERGE` (or none). In [durable mode](#durable-execution-checkpoint-replay) branches run sequentially in edge order |
| **Subgraphs**           | `SUBGRAPH` nodes run a nested `WorkflowDefinition` (own context and `max_steps` budget); the nested output becomes the node output and a failed nested run fails the node |
| **Condition branching** | Safe AST-based expression evaluation (`core/workflows/conditions.py`); edges are chosen by their `condition_label` (`"true"`/`"false"`) |
| **Cycles / evaluation loops** | Traversal is iterative, so a CONDITION edge looping back to an earlier node (generate → evaluate → refine) executes correctly; `WorkflowExecutor(max_steps=...)` (default 1000) fails a loop that never converges |
| **Fail-fast**           | A failed node (after its retries) is recorded then re-raised, halting the run (status `FAILED`) |
| **Context propagation** | Each node reads upstream output via `context.get_last_output()`; non-condition/parallel nodes follow the first outgoing edge |

### Durable execution (checkpoint replay)

`execute(..., checkpoint=...)` takes an optional
[`CheckpointManager`](orchestration.md#durable-checkpointing-resume). When
given, **every node execution is recorded** through
`CheckpointManager.run_step` under the deterministic key
`workflow:<node_id>` — a resumed run replays completed nodes' outputs from
the store **without re-executing them**, so a crash mid-graph never
duplicates a node's side effects:

```python
result = await executor.execute(
    workflow=wf,
    initial_input={"query": "research topic"},
    checkpoint=context["checkpoint"],   # from the orchestration context
)
```

Semantics to know:

- **One step per node visit.** The whole timeout + retry sequence of a node
  is a single recorded step: replay returns the node's *final* output
  regardless of how many retries the original run needed, keeping replay
  cursors aligned.
- **JSON-serializable outputs.** Durable runs require node outputs to be
  JSON-serializable — that is the persistent checkpoint store's contract.
- **Sequential `PARALLEL` branches.** In durable mode fan-out branches
  execute sequentially in edge order: replay cursors must assign the same
  key to the same node on every pass, and concurrent per-step saves would
  interleave version bumps in the store. Without a checkpoint, branches run
  concurrently via `asyncio.gather` as before.

Without a `checkpoint` argument, execution behavior is unchanged.

### Human approval gates (`HUMAN` nodes)

A `HUMAN` node is a **durable approval gate** in the graph
(`handle_human` in `core/workflows/node_handlers.py`). It reuses the pause
contract of the ReAct autonomy gate — persist `awaiting_approval`, raise
`ApprovalPendingError` — so the same
[`/approvals` API](orchestration.md#durable-human-in-the-loop-approvals-pause-decide-resume)
drives both: record the reviewer's decision, then resume the run.

```python
wf = (
    WorkflowBuilder(name="deploy-pipeline")
    .start()
    .agent("Draft Change", agent_id="coder")
    .human("Review Change", category="deployment")
    .agent("Apply Change", agent_id="deployer")
    .end()
    .build()
)
```

Semantics, in execution order:

- **Fresh gate (durable run)** — with a checkpoint on the context, the gate
  persists the run as `awaiting_approval` (node id + `config["category"]`,
  default `"human_gate"`) and raises `ApprovalPendingError`. The executor
  propagates it untouched: it is **never retried** (a durable pause is not a
  retryable failure) and **never converted to a `FAILED` node result** — the
  orchestrator surfaces the usual `{"awaiting_approval": true, "run_id": ...}`
  response and the run survives restarts.
- **Approved resume** — `process(run_id=..., resume=True)` replays earlier
  nodes from the checkpoint (no re-execution), the gate consumes the recorded
  decision and passes the **last output through** unchanged. The consumed gate
  is itself recorded as a replay step, so multiple gates in one graph work:
  each pause/decide/resume cycle replays past every previously approved gate
  and stops at the next fresh one.
- **Denied resume** — the gate raises, the node fails, and the workflow ends
  `FAILED`.
- **No checkpoint** — the gate **fails closed** with a `RuntimeError` telling
  you to execute with durable checkpointing (e.g. via the orchestrator with
  `ORCHESTRATOR_CHECKPOINT_ENABLED`). A human gate must never silently wave
  traffic through — which is exactly what the old handler-less pass-through
  did.

### ExecutionStatus Values

```python
from core.workflows.executor import ExecutionStatus

ExecutionStatus.PENDING
ExecutionStatus.RUNNING
ExecutionStatus.COMPLETED
ExecutionStatus.FAILED
ExecutionStatus.CANCELLED
```

---

## Scheduled workflows (`WorkflowScheduler`)

`WorkflowDefinition` carries two optional scheduling fields, both serialized
by `to_dict()`/`from_dict()`:

- `schedule` — a 5-field cron expression (see
  [Task Queue › Cron Expressions](task-queue.md#cron-expressions-cronexpression)).
  Validated at **construction time**: a malformed expression raises
  `ValueError` when the definition is built, not when it first fires.
- `on_failure` — a webhook event name (e.g. `"workflow.failed"`) emitted when
  a scheduled run of this workflow fails.

`WorkflowScheduler` (`core/workflows/schedule.py`, exported from
`core.workflows`) keeps a registry of scheduled definitions and executes the
due ones through a `WorkflowExecutor`:

```python
import asyncio
from core.workflows import WorkflowDefinition, WorkflowExecutor, WorkflowScheduler
from core.webhooks import get_webhook_service

nightly = WorkflowDefinition(
    name="nightly-report",
    schedule="0 2 * * *",           # 02:00 UTC every day
    on_failure="workflow.failed",   # emitted when a scheduled run fails
)
# ... add nodes/edges ...

scheduler = WorkflowScheduler(
    WorkflowExecutor(),                      # or a zero-arg factory for fresh executors
    webhook_service=get_webhook_service(),   # optional — enables on_failure emission
)
first_fire = scheduler.register(nightly)     # returns the first fire time (UTC)

# Deterministic single pass (tests, external tick sources):
results = await scheduler.run_due(now)       # list[WorkflowResult], fire order

# Or a thin polling loop as a task of your own:
task = asyncio.create_task(scheduler.run_forever(interval=30.0))
```

Design points, all verified behavior:

- **Deterministic by design** — time is injected via a `clock=` override,
  `due(now)` / `run_due(now)` take an explicit `now` (naive treated as UTC),
  and the class spawns no background tasks itself; `run_forever` is a thin
  loop callers own and cancel.
- **Reschedules before executing** — `run_due` computes each entry's next
  fire time *before* running it, so a long or crashing run cannot stall or
  double-fire the schedule.
- **Contains executor exceptions** — an exception from `execute()` is
  converted into a `FAILED` `WorkflowResult` per workflow; one broken
  workflow never takes down the pass.
- **`on_failure` is best-effort** — a failed run emits the workflow's
  `on_failure` event (payload: `workflow_id`, `workflow_name`, `schedule`,
  `status`, `error`) through the [webhook service](webhooks.md); emit errors
  are logged, never raised.
- `register` raises `ValueError` for a definition without a `schedule`;
  `unregister(workflow_id)` and `next_run_at(workflow_id)` complete the
  registry surface.

---

## Orchestrator Bridge — `WorkflowFlowHandler`

`WorkflowFlowHandler` (`core/workflows/flow_handler.py`, exported from
`core.workflows`) exposes a `WorkflowDefinition` behind the standard
[`FlowHandler` protocol](orchestration.md#flow-handler-protocol), so a
declarative graph can be registered for an intent exactly like any imperative
handler:

```python
from core.workflows import WorkflowFlowHandler

orchestrator.register_handler("report_pipeline", WorkflowFlowHandler(wf))
```

- **Constructor** — `WorkflowFlowHandler(workflow, executor=None)`: pass a
  pre-configured `WorkflowExecutor` (agents/tools registries, `max_steps`) or
  get a default one.
- **Durable by inheritance** — the bridge passes the orchestration context's
  `context["checkpoint"]` (present when checkpointing is enabled, which is
  the default) into `execute()`, so every node is recorded and a resumed run
  replays completed nodes.
- **Result shape** — the orchestrator result dict: `response` carries the
  graph's final output, `metadata` its execution summary (`workflow`,
  `workflow_id`, `nodes_executed`, `duration_ms`). A failed run comes back as
  a structured error result (`error: True`), not an exception.
- **Approval pauses propagate** — an `ApprovalPendingError` from a
  [`HUMAN` gate](#human-approval-gates-human-nodes) is *not* folded into an
  error result: it passes through the bridge so the orchestrator answers with
  the standard `awaiting_approval` response and the `/approvals` API can
  resume the run.

---

## Flow Designer Integration

Workflows are serializable to/from JSON, making them compatible with the **Native Flow Designer** frontend widget:

```python
# Save workflow to database
json_str = workflow.to_json()
await db.save_workflow(workflow_id, json_str)

# Load and execute from storage
json_str = await db.get_workflow(workflow_id)
wf = WorkflowDefinition.from_json(json_str)
result = await executor.execute(wf, initial_input={...})
```

!!! tip "Visual Editor"
    Each `WorkflowNode` has a `position` field (`x`, `y`) for the visual drag-and-drop editor. These are saved and restored automatically with `to_json()`/`from_json()`.
