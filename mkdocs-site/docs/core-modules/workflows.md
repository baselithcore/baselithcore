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

```python
from core.workflows.builder import WorkflowDefinition, WorkflowNode, NodeType

# Define a simple linear workflow
wf = WorkflowDefinition(name="research-pipeline", description="Research and report")

# Add nodes
start = wf.add_node(WorkflowNode(id="start", type=NodeType.START, label="Start"))
search = wf.add_node(WorkflowNode(
    id="search",
    type=NodeType.AGENT,
    label="Web Search",
    agent_id="researcher-agent",
    config={"max_results": 10},
    timeout=30.0,
))
report = wf.add_node(WorkflowNode(
    id="report",
    type=NodeType.AGENT,
    label="Report Writer",
    agent_id="writer-agent",
))
end = wf.add_node(WorkflowNode(id="end", type=NodeType.END, label="End"))

# Connect nodes
wf.add_edge(start, search)
wf.add_edge(search, report)
wf.add_edge(report, end)

# Serialize to JSON (for storage or the Flow Designer UI)
json_str = wf.to_json()

# Deserialize
wf2 = WorkflowDefinition.from_json(json_str)
```

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
| `LOOP`      | Iterative loop             | `config.max_iterations`  |
| `HUMAN`     | Human-in-the-loop pause    | `config.timeout_seconds` |
| `TRANSFORM` | Data transformation        | `config.expression`      |

### Condition Expressions

Condition nodes use a **safe AST evaluator** — no `eval()`, no code injection risk:

```python
wf.add_node(WorkflowNode(
    id="check-confidence",
    type=NodeType.CONDITION,
    label="High Confidence?",
    condition_expression="score > 0.85 and status == 'ok'",
    # Variables 'score' and 'status' come from the workflow context
))
```

Supported operators: `==`, `!=`, `<`, `>`, `<=`, `>=`, `in`, `not in`, `is`, `is not`, `and`, `or`, `not`, `+`, `-`, `*`, attribute access.

---

## WorkflowExecutor

Executes a `WorkflowDefinition` asynchronously, step by step.

```python
from core.workflows.executor import WorkflowExecutor

executor = WorkflowExecutor()

# Register handlers for node types
@executor.register_handler(NodeType.AGENT)
async def handle_agent(node, context):
    agent = get_agent(node.agent_id)
    return await agent.run(context.get_last_output())

# Execute
result = await executor.execute(
    workflow=wf,
    initial_input={"query": "research topic"},
)
print(result.status)       # ExecutionStatus.COMPLETED
print(result.outputs)      # Dict[node_id, Any]
print(result.execution_ms) # Total time in milliseconds
```

### Execution Features

| Feature                 | Description                                                          |
| ----------------------- | -------------------------------------------------------------------- |
| **Timeouts**            | Per-node `timeout` field (`asyncio.wait_for`)                        |
| **Parallel**            | `PARALLEL` nodes execute branches concurrently with `asyncio.gather` |
| **Condition branching** | Safe AST-based expression evaluation                                 |
| **Error isolation**     | Failed nodes record error without halting sibling branches           |
| **Context propagation** | Each node receives output of upstream nodes                          |

### ExecutionStatus Values

```python
from core.workflows.executor import ExecutionStatus

ExecutionStatus.PENDING
ExecutionStatus.RUNNING
ExecutionStatus.COMPLETED
ExecutionStatus.FAILED
ExecutionStatus.TIMEOUT
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
