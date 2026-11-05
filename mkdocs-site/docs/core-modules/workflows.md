# Workflow Engine

The `baselith/core/workflows/` module provides a **graph-based workflow engine** for building, serializing, and executing baselith-core pipelines. Workflows are defined as directed graphs of typed nodes, with support for branching, parallelism, loops, and human-in-the-loop steps.

To load definitions from files, run them over HTTP, trigger them with signed webhooks, schedule them and expose them as MCP tools, see [Workflow Catalog & API](workflow-catalog.md).

## Module Structure

```txt
baselith/core/workflows/
├── builder.py        # WorkflowDefinition, WorkflowNode, WorkflowEdge
├── executor.py       # WorkflowExecutor — async graph execution
├── node_handlers.py  # Default handlers per NodeType (+ install_defaults)
├── nodes_data.py     # TEMPLATE, ASSIGN, ITERATION handlers
├── nodes_ai.py       # LLM, CLASSIFIER, RETRIEVE handlers
├── nodes_http.py     # HTTP request handler (SSRF-guarded)
├── nodes_list.py     # LIST handler (declarative list reshaping)
├── nodes_code.py     # CODE handler (sandboxed Python, opt-in)
├── generator.py      # Draft a definition from a description, validated
├── generator_reference.py # The node reference shown to the drafting model
├── nodes_validation.py # Static per-node configuration checks
├── _scope.py         # Run scope + {{ }} template renderer
├── conditions.py     # Safe AST condition evaluator
├── serialization.py  # YAML/JSON loading and export (hardened)
├── catalog.py        # WorkflowCatalog — named definitions, directory-backed
├── runtime.py        # WorkflowRuntime — limits, history, approval pauses
├── triggers.py       # Inbound trigger signature verification
├── service.py        # WorkflowService — catalog + runtime + scheduler + MCP sync
├── mcp_tools.py      # Catalog workflows as MCP tools
├── adapters.py       # CrewNodeAdapter / ColonyNodeAdapter — multi-agent primitives behind the AGENT-node contract
├── schedule.py       # WorkflowScheduler — in-process cron scheduling
├── versioning.py     # Pins a definition to a durable run
├── durable.py        # Durable node visits: one step per visit + the visit's child scope
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
from baselith.core.workflows.builder import (
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

# YAML works the same way — reviewable, diffable, loadable by the catalog
yaml_str = wf.to_yaml()
wf3 = WorkflowDefinition.from_yaml(yaml_str)   # also accepts JSON text
```

`from_yaml` / `load_definition` use the safe loader, refuse text over 2 MiB
and **reject anchors and aliases**, and raise `DefinitionError` (a
`ValueError`) for anything that is not a usable definition. A node whose
`config` holds a callable (`transform`, `fn`, `agent`) cannot be exported;
`to_yaml()` says so rather than writing something that cannot be loaded back.

### Fluent: `WorkflowBuilder`

```python
from baselith.core.workflows.builder import WorkflowBuilder

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
`transform`, `parallel`, `merge`, `human`, `subgraph`, `llm`, `retrieve`, `http`,
`template`, `assign`, `iterate`, `list_ops`, `code`) returns the builder for
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
| `LLM`       | Language-model call with a templated prompt ([details](#llm)) | `config["prompt"]`, `config["schema"]` |
| `CLASSIFIER` | N-way routing on a declared label set ([details](#classifier)) | `config["categories"]`, labelled outgoing edges |
| `RETRIEVE`  | Knowledge-base lookup ([details](#retrieve)) | `config["query"]`, `config["top_k"]` |
| `HTTP`      | Outbound HTTP request, SSRF-guarded ([details](#http-http-request)) | `config["url"]`, `config["method"]` |
| `TEMPLATE`  | Render text or a structure from earlier results ([details](#template-assign-and-iteration)) | `config["template"]` |
| `ASSIGN`    | Set run variables ([details](#template-assign-and-iteration)) | `config["assign"]`, `config["coalesce"]` |
| `ITERATION` | Run a nested workflow once per list item ([details](#template-assign-and-iteration)) | `config["workflow"]`, `config["items"]` |
| `LIST`      | Filter, sort, slice, de-duplicate and project a list ([details](#list)) | `config["ops"]` |
| `CODE`      | Python snippet in the sandbox, opt-in ([details](#code)) | `config["code"]` |

!!! note "Node fields"
    `TOOL` nodes call the tool with the upstream output; with
    `config["arguments"]` (a mapping, `{{ }}` templates allowed) they call it
    with those keyword arguments instead, which is the shape plugin tools are
    registered in.
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

Any unsupported node or an undefined variable raises `ValueError`. Chained
comparisons (`a < b < c`) pair each operator with its right-hand comparator
via `zip(node.ops, node.comparators, strict=True)` — the two lists are always
the same length for a well-formed AST, and `strict=True` makes that invariant
explicit rather than silently truncating if it were ever violated.

---

## Templates

The data and model nodes build their text and structures from earlier results
with `{{ path }}` placeholders. A placeholder resolves against a small,
data-only scope:

| Name | Value |
| ---- | ----- |
| `input` | The run's initial input |
| `output` | The output of the node that ran last |
| `nodes` | `{node_id: output}` for every node executed so far |
| any variable | Whatever an [`ASSIGN`](#template-assign-and-iteration) node (or `context.set_variable`) set |

`input`, `output` and `nodes` are reserved: an `ASSIGN` node may not shadow
them. A path walks mapping keys and list indexes: `nodes.find[0].content`,
`input.user.email`, `nodes["fetch-user"].name` (a quoted key for odd names).
It never reads an attribute or calls anything, so a definition uploaded
through the API cannot reach beyond the data it is handed.

Filters chain with `|`: `{{ nodes.find | pluck(content) | join('\n---\n') }}`.

| Filter | Effect |
| ------ | ------ |
| `default(x)` | `x` when the reference is missing, `null` or empty |
| `json` | JSON-encode the value |
| `pluck(key)` | `[item[key] for item in list]` |
| `join(sep)` | Join a list into a string |
| `first`, `last` | First or last item of a non-empty list |
| `length` | Length of a string, list or mapping |
| `upper`, `lower`, `trim`, `truncate(n)` | String helpers |

Two behaviors are deliberate:

- **An unresolved reference fails the node.** Rendering an empty string would
  send a half-built prompt or request body to a model or a remote service;
  `| default(…)` is the explicit opt-out.
- **A string that is exactly one placeholder keeps its type.** `"{{ nodes.find }}"`
  yields the list, not its JSON text; `"found {{ nodes.find | length }}"` is a
  string. Structured config values (HTTP `json`, `arguments`, `assign`) are
  rendered recursively, values only: keys are never templated.

A rendered string is capped at 1,000,000 characters.

### LLM

Calls the framework's [LLM service](../advanced/inference-services.md) (or the
executor's `services["llm"]`).

| Config | Meaning |
| ------ | ------- |
| `prompt` | User prompt, templated. Required |
| `system` | System prompt, templated |
| `model`, `temperature`, `max_tokens` | Passed through to the service |
| `json` | Ask for JSON and return it parsed |
| `schema` | A JSON Schema. The answer is requested in that shape through the structured-output path, **validated**, and returned parsed. A violation, invalid JSON or a truncated answer fails the node |

Per-node `retries` and `timeout` apply as for any node.

### CLASSIFIER

Routes the run to one of several branches. Every outgoing edge needs a
`condition_label`; the node's result is the chosen label and the executor
follows the edge carrying it. Edges labelled `default` or `else` catch any
class without an edge of its own.

| Config | Meaning |
| ------ | ------- |
| `categories` | Two or more labels, as strings or `{label, description, keywords}` mappings |
| `text` | What to classify, templated; defaults to `{{ output }}` |
| `default` | Label used when no class matches; must be one of the labels |
| `use_llm` | `false` classifies by keywords only (default `true`) |

Exactly one whole-word keyword hit (case-insensitive) decides **without a model
call**. Zero hits or several go to the model, which must answer with one of the
declared labels (the request carries an `enum`). An answer outside the labels,
or invalid JSON, counts as a miss: the `default` applies, and with none the run
fails. A provider outage is not a miss and fails the node.
`validate()` rejects a classifier with an unlabelled edge or a class that has no
edge and no `default` edge.

!!! warning "A classifier decides on text an attacker may have written"
    The prompt tells the model to treat the text as data, and the answer is
    accepted only from the declared labels, so injected text can at most pick a
    wrong *declared* branch. Put branches that cause side effects behind a
    [`HUMAN` gate](#human-approval-gates-human-nodes).

### RETRIEVE

Semantic search through the vector store (or the executor's
`services["retriever"]`), with the usual tenant isolation.

| Config | Meaning |
| ------ | ------- |
| `query` | Search text, templated; defaults to `{{ output }}`. Empty fails the node |
| `top_k` | Hits to return, 1 to 50 (default 5) |
| `collection` | Collection name; the configured default when omitted |
| `min_score` | Drop hits scoring below it |

The output is `[{"id", "content", "score", "metadata"}, …]`, best first. An
empty list means nothing relevant was found; a store failure raises instead of
returning an empty list.

### HTTP (HTTP request)

| Config | Meaning |
| ------ | ------- |
| `url` | Target, templated. `http` and `https` only |
| `method` | `GET` (default), `POST`, `PUT`, `PATCH`, `DELETE` |
| `headers`, `query` | Templated. `Host`, `Content-Length`, `Transfer-Encoding`, `Connection` and similar are dropped |
| `json` / `body` | Request body (templated; `json` keeps types) |
| `credential` | Name of an operator-configured credential; sent only to its declared hosts, over https |
| `timeout` | Seconds, at most 120 (default 30) |
| `max_response_bytes` | At most 10 MiB (default 1 MiB) |
| `allow_error_status` | Return 4xx/5xx instead of failing the node |

The output is `{"status", "ok", "content_type", "body"}`; `body` is parsed JSON
for JSON responses and text otherwise. A non-2xx status fails the node (so
`retries` applies) unless `allow_error_status` is set.

The URL is attacker-influenced, so the node uses the framework's [egress
screening](../advanced/security.md): scheme and host checks, DNS resolution
with the connection **pinned to the vetted address**, no redirects, no
keep-alive, and a capped response. Loopback, private, link-local and reserved
targets are refused unless `WORKFLOWS_HTTP_ALLOW_INTERNAL` is set (local
development only).

Credentials never live in a definition. A node names one
(`credential: crm`) and the operator maps the name to the headers to send *and the
hosts they are for*:

```json
{"crm": {"hosts": ["api.crm.example"], "headers": {"Authorization": "Bearer …"}}}
```

That JSON is `WORKFLOWS_HTTP_CREDENTIALS` for catalog workflows; in code it is
`WorkflowExecutor(services={"http_credentials": {...}})`, whose header values may
be `SecretStr`.

A credential is **bound to its hosts**, because the URL of a node is often built
from data the workflow does not control (a webhook payload, a model's answer),
and a token that follows whatever URL it is handed is a token delivered to
whoever can steer the URL:

- the host of the URL, parsed exactly as the egress screen parses it, must be one
  of the credential's `hosts` (compared whole, case-insensitive, trailing dot
  ignored, so `api.crm.example.evil.test`, `sub.api.crm.example` and
  `https://api.crm.example@evil.test/` are all refused);
- the check runs after the egress screen and before anything is sent, so a refused
  node sends nothing;
- a credential is attached only over `https` (plain `http` is allowed only under
  `WORKFLOWS_HTTP_ALLOW_INTERNAL`, the local-development posture);
- the credential's headers win over a header of the same name set in the
  definition;
- an entry with no `hosts` or no `headers` is **dropped and logged**, never widened
  to "any host", so a node that uses it fails.

### TEMPLATE, ASSIGN and ITERATION

- **`TEMPLATE`** renders `config["template"]` and returns the result (typed when
  the template is a single placeholder).
- **`ASSIGN`** sets run variables and passes the previous output through.
  `config["assign"]` maps a name to a value or template, all rendered against
  the scope *before* the node ran, so entry order does not matter.
  `config["coalesce"]` is `{"into": name, "paths": […]}` and stores the first
  path that resolves to a non-empty value (the run fails if none does).
  Variable names are `[A-Za-z_][A-Za-z0-9_]{0,63}` and may not be reserved.
- **`ITERATION`** runs `config["workflow"]` (a definition or its `to_dict()`)
  once per item of `config["items"]` (a template, default `{{ output }}`),
  with the item as that run's input, and returns the list of outputs in item
  order. `concurrency` (1 to 16) runs items together; `max_items` (default 500,
  ceiling 10000) refuses longer lists; `on_error: continue` records
  `{"error": …}` for a failed item instead of stopping. With `concurrency > 1`
  a failure cancels the items still running. In
  [durable mode](#durable-execution-checkpoint-replay) items run one at a time,
  each in its own child scope, so a resumed run replays the finished ones.

### LIST

Reshapes a list without code. `config["items"]` is a template (default
`{{ output }}`); `config["ops"]` is a sequence of one-key steps applied in order:

| Step | Effect |
| ---- | ------ |
| `filter: "<expression>"` | Keep items for which the [safe expression](#condition-expressions) is true; `item` is the element and `index` its position (`item['score'] > 0.5`) |
| `sort: {key, descending}` | Stable sort by a key path inside each item; `sort: <key>` is shorthand, and a missing key sorts the items themselves |
| `reverse: true` | Reverse the list |
| `unique: <key>` or `true` | Keep the first item per key value (or per whole value) |
| `offset: n`, `limit: n` | Skip and take (non-negative integers) |
| `pluck: <key>` | Replace each item by the value at `key` |
| `flatten: true` | Merge one level of nested lists |

```yaml
- id: best
  type: list
  config:
    items: "{{ nodes.find }}"
    ops:
      - filter: "item['score'] > 0.5"
      - sort: {key: score, descending: true}
      - unique: document_id
      - limit: 3
      - pluck: content
```

A step that cannot be applied fails the node: a filter expression that raises on
an item, a key that is missing from an item, values that cannot be compared. It
never silently drops items. Lists longer than 100,000 items are refused, and
`validate()` rejects an unknown step name or a step that is not a one-key mapping
before the workflow runs.

### CODE

Runs a Python snippet in the **sandbox**, never in the framework process: the
same isolated Docker or microVM environment the coding agents use, with its
resource ceilings and static pre-screen (`SANDBOX_*`).

It is **off by default**. A definition can be uploaded through the API, and a node
that executes code turns "may save a workflow" into "may run code", so an operator
enables it on purpose with `WORKFLOWS_CODE_ENABLED=true`. Until then the node fails
closed and the sandbox is never reached.

| Config | Meaning |
| ------ | ------- |
| `code` | Python source. Required |
| `inputs` | A mapping of templated values handed to the code as JSON; default `{"input": "{{ output }}"}` |
| `timeout` | Seconds, clamped by the sandbox's own ceiling |

The inputs arrive in the `WORKFLOW_INPUT` environment variable (at most 64 KiB; it
is not a data channel for large payloads), and the code returns its result by
printing it:

```python
import json, os

data = json.loads(os.environ["WORKFLOW_INPUT"])
print(json.dumps({"total": sum(data["numbers"])}))
```

Stdout is parsed as JSON when it parses and returned as text otherwise. A
non-zero exit fails the node with the last 500 characters of stderr. Only
`python` is supported.

### Drafting a workflow from a description

`generate_workflow(request, llm=None, existing=None, max_attempts=3)`
(`baselith/core/workflows/generator.py`) asks the language model for a definition
and returns it only if it is valid:

- the reply is parsed with the same hardened loader as any other definition (no
  anchors or aliases, size capped) and must pass `validate()`;
- when it does not, the errors go back to the model for another attempt, up to
  `max_attempts` (1 to 5); a draft that never validates raises `GenerationError`
  with the last errors and is never returned half-valid;
- the description is placed inside `<request>` delimiters and the system prompt
  says it is data, not instructions; the prompt forbids secrets (an HTTP node
  names a credential, it does not carry one) and asks for a `human` gate before
  side effects;
- with `existing=` the model revises a definition and the id is kept;
- a draft with no usable id gets one, and the result says so in `warnings`.

The result is a draft for a person to review. Nothing is saved, scheduled or run,
and the usual gates apply when it later runs (CODE nodes need
`WORKFLOWS_CODE_ENABLED`, tools come from the executor's registry). The node
reference the model sees lives in `generator_reference.py`, and a test fails the
build if a `NodeType` is missing from it. From the command line:
`baselith workflow generate "…"`; over HTTP: `POST /workflows/generate`.

---

## WorkflowExecutor

Executes a `WorkflowDefinition` asynchronously, step by step.

Default handlers cover every node type (bodies in
`baselith/core/workflows/node_handlers.py`); `LOOP`'s default fails closed (see the
node-fields note above). `register_handler` is a regular method (not a
decorator). Handlers receive `(node, context)` and may be sync or async —
registering one overrides the default for that type.

`AGENT` nodes resolve `config["agent"]` (any object with `async run(prompt)`,
e.g. [`baselith.core.agent.Agent`](agent.md)) or their `agent_id` in the executor's
`agents` registry; the prompt is `config["prompt"]` with `{input}` replaced
by the upstream output, or the upstream output itself. `TOOL` nodes resolve
`config["fn"]` or their `tool_id` in the `tools` registry and are called with
the upstream output:

```python
from baselith.core.agent import Agent
from baselith.core.workflows.executor import WorkflowExecutor

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

`WorkflowExecutor(services=…)` supplies the collaborators of the model and I/O
nodes; each is optional and falls back to the framework default:

| Key | Used by | Value |
| --- | ------- | ----- |
| `llm` | `LLM`, `CLASSIFIER` | An object with `generate_response(prompt, …)` and `generate(prompt, response_format=…, …)`, such as the LLM service |
| `retriever` | `RETRIEVE` | `async (query, *, top_k, collection) -> list[dict]` |
| `http_credentials` | `HTTP` | `{name: {"hosts": [...], "headers": {header: value}}}`; header values may be `SecretStr` |
| `http_client` | `HTTP` | An `httpx.AsyncClient` (tests, custom transports) |
| `code_runner` | `CODE` | `async (code, *, envs, timeout) -> result` with `stdout`, `stderr`, `exit_code`; the sandbox service by default |

### Crews as agent nodes — `CrewNodeAdapter`

The AGENT-node contract is `async run(prompt)` returning an object with
`.output`. A [`Crew`](agent.md#multi-agent-crews-crew-task) speaks a different
dialect — `async run(inputs)` returning a `CrewResult` — so
`CrewNodeAdapter` (`baselith/core/workflows/adapters.py`, exported from
`baselith.core.workflows`) bridges it onto the contract: the node's prompt is bound
under `input_key` (default `"input"`, referenced by task descriptions as
`{input}`), and `CrewResult.final` becomes the node output. A whole crew then
composes into a graph as one node. In
[durable mode](#durable-execution-checkpoint-replay) a completed crew node
replays its recorded output on resume, and a crash *inside* the crew resumes
the crew where it stopped: the adapter passes the node visit's checkpoint
scope to `Crew.run(checkpoint=...)`, so finished tasks replay and only the
interrupted task runs again (its tool calls replay too):

```python
from baselith.core.agent import Agent, Crew, Task
from baselith.core.workflows import CrewNodeAdapter, WorkflowExecutor
from baselith.core.workflows.builder import WorkflowBuilder

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
(`baselith/core/workflows/adapters.py`, exported from `baselith.core.workflows`) bridges it onto
the AGENT-node contract: the node's prompt becomes a `Task`
(`description=prompt`, filtered by the adapter's `required_capabilities`), the
colony's auction picks the executing agent, and the caller-supplied
`execute_fn` — the same async `(task, agent) -> result` callback contract as
`execute_batch` — performs the work. The single task's output becomes the node
output; a failed **or unassigned** task (no agent won the auction) raises, so
the graph's per-node retry/error semantics apply:

```python
from baselith.core.swarm import AgentProfile, Capability, Colony, Task
from baselith.core.workflows import ColonyNodeAdapter, WorkflowExecutor
from baselith.core.workflows.builder import WorkflowBuilder

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
primitives compose into graphs: a **crew** node when the collaboration is a
fixed task pipeline, a **colony** node when the executing agent should be
chosen by the auction per task. In [durable mode](#durable-execution-checkpoint-replay)
either node's output replays once it completes. Inside a running node, a
**crew** resumes task by task (above); a **colony** node binds the visit's
scope as the ambient checkpoint for the batch, which records the auction
outcome (every bid and the winner) and the task's result under it
([Swarm › durable colony batches](swarm.md#durable-colony-batches)): a node
interrupted after the auction reinstates the recorded winner instead of
re-auctioning, and `execute_fn` runs with the task's own scope bound, so work
it does through a durable runner (an `Agent`, a `Crew`) records there. An
`execute_fn` that calls effects directly is still only as idempotent as the
code it calls.

### Execution Features

| Feature                 | Description                                                          |
| ----------------------- | -------------------------------------------------------------------- |
| **Timeouts**            | Per-node `timeout` field (`asyncio.wait_for`); a timeout fails the node |
| **Per-node retry**      | `retries` + `retry_backoff` fields: extra attempts with exponential backoff; timeouts are never retried |
| **Parallel + fan-in**   | `PARALLEL` nodes fan out to all outgoing edges with `asyncio.gather`; branches halt at their convergence `MERGE` node, which then executes **once** with the list of branch outputs and the chain continues past it. Branches must converge on the same `MERGE` (or none). In [durable mode](#durable-execution-checkpoint-replay) branches run sequentially in edge order |
| **Subgraphs**           | `SUBGRAPH` nodes run a nested `WorkflowDefinition` (own context and `max_steps` budget); the nested output becomes the node output and a failed nested run fails the node. In [durable mode](#durable-execution-checkpoint-replay) the nested workflow runs under the node visit's scope, so its finished inner nodes replay |
| **Condition branching** | Safe AST-based expression evaluation (`baselith/core/workflows/conditions.py`); edges are chosen by their `condition_label` (`"true"`/`"false"`) |
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
  regardless of how many retries the original run needed. Steps are keyed
  by node and occurrence (`v2:workflow:<node>:<hash>:<n>`), not by position,
  so a loop that visits a node twice records two steps and a resumed run
  matches them in visit order of that node alone.
- **Work inside a node is durable too.** Each visit gets its own child scope
  of the run's checkpoint (`baselith/core/workflows/durable.py`; see
  [composite runners](orchestration.md#composite-runners-child-scopes-and-what-each-one-records)).
  An AGENT node passes it to `agent.run(prompt, checkpoint=...)` when the
  agent accepts one — the typed `Agent`, `CrewNodeAdapter`,
  `ColonyNodeAdapter` — so a node interrupted half-way replays the tool calls
  or crew tasks it had finished, and the scope is bound as the ambient
  checkpoint for custom handlers. Agents whose `run` takes no `checkpoint`
  re-run whole, as before.
- **Subgraphs resume inside.** A SUBGRAPH node executes its nested workflow
  with the visit's scope as that workflow's `checkpoint`, so every inner node
  is a recorded step of the scope: a subgraph interrupted half-way replays the
  inner nodes it had finished and re-executes from the first unfinished one,
  and once it completes the whole node replays as one step. The nested
  definition is version-pinned under its own key
  (`workflow_version@<scope path>`, see `pin_key` in `versioning.py`) — sharing
  the run's pin would always mismatch the parent graph. A HUMAN gate inside a
  subgraph pauses the whole run, exactly as one at the top level.
- **JSON-serializable outputs.** Durable runs require node outputs to be
  JSON-serializable — that is the persistent checkpoint store's contract.
- **Sequential `PARALLEL` branches.** In durable mode fan-out branches
  execute sequentially in edge order: concurrent per-step saves would
  interleave version bumps in the store. Without a checkpoint, branches run
  concurrently via `asyncio.gather` as before.

Without a `checkpoint` argument, execution behavior is unchanged.

### Human approval gates (`HUMAN` nodes)

A `HUMAN` node is a **durable approval gate** in the graph
(`handle_human` in `baselith/core/workflows/node_handlers.py`). It reuses the pause
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
- **Catalog workflows** — a workflow run through the
  [catalog API](workflow-catalog.md#approval-pauses) pauses at the gate with
  status `awaiting_approval`; record the decision with `/approvals` and resume
  with `POST /workflows/runs/{run_id}/resume`.

### ExecutionStatus Values

```python
from baselith.core.workflows.executor import ExecutionStatus

ExecutionStatus.PENDING
ExecutionStatus.RUNNING
ExecutionStatus.COMPLETED
ExecutionStatus.FAILED
ExecutionStatus.CANCELLED
```

---

## Version pinning (durable runs)

A durable run outlives the process that started it — and the *deployment*. The
checkpoint is resumed hours later, by a pod running whatever definition the
current release ships. If that definition changed (a node inserted, an edge
rerouted, a condition rewritten) the resumed run replays recorded outputs into
a graph that no longer matches them. Nothing errors; the run simply takes a
path it was never on.

`WorkflowExecutor.execute` therefore **pins** the definition to the run on its
first pass, and verifies it on every resume. The pin lives in
`Checkpoint.plugin_data`, which already survives a resume — no schema change,
and a checkpoint written before this existed simply has no pin and gets one.

```python
from baselith.core.workflows import definition_fingerprint, pin_version

pin_version(workflow)
# WorkflowVersion(version='1.0.0', fingerprint='9f2c…')
```

`version` alone is not enough: it is a string a human maintains, and the edit
this guards against is precisely the one nobody thought to bump a version for.
So the pin carries a **fingerprint** as well — a digest of the graph's
executable structure.

| Covered by the fingerprint | Deliberately ignored |
| --- | --- |
| node ids, types, `config` | node `label` |
| `agent_id`, `tool_id` | canvas `position` |
| `condition_expression` | workflow `name`, `description` |
| `timeout`, `retries`, `retry_backoff` | `metadata`, timestamps |
| every edge, including `condition_label` | node and edge storage order |

Cosmetic edits are excluded on purpose: a fingerprint that invalidates every
live run because someone renamed a label gets switched off, and then it guards
nothing.

`BASELITH_WORKFLOW_VERSION_PINNING` selects the stage:

| Value | Behaviour |
| --- | --- |
| `off` | Pins are still recorded; mismatches ignored. |
| `warn` | The mismatch is logged and the run continues on the pinned identity. |
| `enforce` *(default)* | The run stops: `execute()` returns a `FAILED` result carrying the mismatch. |

`enforce` is safe as the default because a run that predates the mechanism
carries no pin — it is pinned on first sight and can never mismatch
retroactively. Only a definition genuinely edited *during* a live run is
refused. A run without a checkpoint is never pinned: there is nothing to
resume, so there is nothing a later deploy could change underneath it.

---

## Scheduled workflows (`WorkflowScheduler`)

!!! note "Wired only through the workflow catalog"
    The default app never constructs a `WorkflowScheduler`. With
    `WORKFLOWS_ENABLED` and `WORKFLOWS_SCHEDULER_ENABLED` the
    [workflow catalog](workflow-catalog.md#scheduling) registers every catalog
    workflow that has a `schedule` and keeps the schedule in step as workflows
    are saved and removed. Anywhere else, a `schedule` fires only once your code
    registers it and drives `run_due` / `run_forever`, as below.

`WorkflowDefinition` carries two optional scheduling fields, both serialized
by `to_dict()`/`from_dict()`:

- `schedule` — a 5-field cron expression (see
  [Task Queue › Cron Expressions](task-queue.md#cron-expressions-cronexpression)).
  Validated at **construction time**: a malformed expression raises
  `ValueError` when the definition is built, not when it first fires.
- `on_failure` — a webhook event name (e.g. `"workflow.failed"`) emitted when
  a scheduled run of this workflow fails.

`WorkflowScheduler` (`baselith/core/workflows/schedule.py`, exported from
`baselith.core.workflows`) keeps a registry of scheduled definitions and executes the
due ones through a `WorkflowExecutor`:

```python
import asyncio
from baselith.core.workflows import WorkflowDefinition, WorkflowExecutor, WorkflowScheduler
from baselith.core.webhooks import get_webhook_service

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

`WorkflowFlowHandler` (`baselith/core/workflows/flow_handler.py`, exported from
`baselith.core.workflows`) exposes a `WorkflowDefinition` behind the standard
[`FlowHandler` protocol](orchestration.md#flow-handler-protocol), so a
declarative graph can be registered for an intent exactly like any imperative
handler:

```python
from baselith.core.workflows import WorkflowFlowHandler

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
