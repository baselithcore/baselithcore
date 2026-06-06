# Workflow Engine

The `core/workflows/` module provides a **graph-based workflow engine** for building, serializing, and executing baselith-core pipelines. Workflows are defined as directed graphs of typed nodes, with support for branching, parallelism, loops, and human-in-the-loop steps.

## Module Structure

```txt
core/workflows/
├── builder.py    # WorkflowDefinition, WorkflowNode, WorkflowEdge
└── executor.py   # WorkflowExecutor — async graph execution
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
`transform`, `parallel`, `merge`) returns the builder for chaining and
auto-connects from the previously added node. `build()` returns the
finished `WorkflowDefinition`.

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
| `LOOP`      | Iterative loop (custom handler) | `config`            |
| `HUMAN`     | Human-in-the-loop pause (custom handler) | `config`   |
| `TRANSFORM` | Data transformation        | `config["transform"]` callable |

!!! note "Node fields"
    `condition_expression` is a top-level field on `WorkflowNode` (not under
    `config`). The default `TRANSFORM` handler looks up a callable at
    `config["transform"]` and applies it to the upstream output, passing the
    input through unchanged if none is set. `LOOP`/`HUMAN` have no default
    handler — register your own.

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

Default handlers are registered only for `START`, `END`, `TRANSFORM`, and
`CONDITION`. Every other node type (`AGENT`, `TOOL`, `LOOP`, `HUMAN`,
`PARALLEL`, `MERGE`) needs a handler registered via `register_handler`,
which is a regular method (not a decorator). Handlers receive
`(node, context)` and may be sync or async:

```python
from core.workflows.executor import WorkflowExecutor

executor = WorkflowExecutor()

# Register a handler for AGENT nodes
async def handle_agent(node, context):
    agent = get_agent(node.agent_id)
    return await agent.run(context.get_last_output())

executor.register_handler(NodeType.AGENT, handle_agent)

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

### Execution Features

| Feature                 | Description                                                          |
| ----------------------- | -------------------------------------------------------------------- |
| **Timeouts**            | Per-node `timeout` field (`asyncio.wait_for`); a timeout fails the node |
| **Parallel**            | `PARALLEL` nodes fan out to all outgoing edges with `asyncio.gather` |
| **Condition branching** | Safe AST-based expression evaluation; edges are chosen by their `condition_label` (`"true"`/`"false"`) |
| **Fail-fast**           | A failed node is recorded then re-raised, halting the run (status `FAILED`) |
| **Context propagation** | Each node reads upstream output via `context.get_last_output()`; non-condition/parallel nodes follow the first outgoing edge |

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
