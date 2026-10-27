---
title: Workflow Catalog & API
description: Load workflows from files, run them over HTTP, trigger them with signed webhooks, schedule them and expose them as MCP tools
---

The [Workflow Engine](workflows.md) executes a `WorkflowDefinition` you hand
it. The **workflow catalog** is what lets a running deployment *own*
definitions: it loads them from a directory of YAML or JSON files, accepts new
ones through an admin API, runs them under shared limits, starts them from
signed inbound webhooks and a cron schedule, and offers the ones you choose as
MCP tools.

Everything here is **opt-in**. With `WORKFLOWS_ENABLED` unset the catalog is
not loaded, no route is mounted and nothing starts.

**Modules**: `baselith/core/workflows/` (`catalog.py`, `runtime.py`,
`service.py`, `triggers.py`, `mcp_tools.py`, `serialization.py`) and the router
in `baselith/plugins/api_routers/routes/workflows.py`.

## Enabling

```bash
WORKFLOWS_ENABLED=true
WORKFLOWS_DIR=./workflows                 # definitions load from, and save to, here
WORKFLOWS_TRIGGER_SECRET=<long random>    # only if you use the inbound trigger
WORKFLOWS_SCHEDULER_ENABLED=true          # only on ONE replica, if you use cron schedules
```

Every variable, its default and its type is in the
[configuration reference](../getting-started/configuration.md#workflow-catalog-and-runtime-configuration).

## Definition files

A definition is a mapping; YAML and JSON are both accepted, and a `.yaml`
file round-trips through `WorkflowDefinition.to_yaml()` / `from_yaml()`.

```yaml
# workflows/support-answer.yaml
id: support-answer
name: Answer a support question from the knowledge base
version: 1.0.0
webhook_trigger: true          # a signed inbound webhook may start it
metadata:
  mcp:
    expose: true               # offer it as the MCP tool "workflow.support-answer"
    category: read_only
    description: Answer a support question using the knowledge base
nodes:
  - {id: start, type: start, label: Start}
  - id: find
    type: retrieve
    label: Find passages
    config: {query: "{{ input.question }}", top_k: 4, min_score: 0.3}
  - id: answer
    type: llm
    label: Answer
    retries: 1
    config:
      system: Answer only from the context. Say so when the context is not enough.
      prompt: |
        Context:
        {{ nodes.find | pluck(content) | join('\n---\n') }}

        Question: {{ input.question }}
  - {id: end, type: end, label: End}
edges:
  - {id: e1, source_id: start, target_id: find}
  - {id: e2, source_id: find, target_id: answer}
  - {id: e3, source_id: answer, target_id: end}
```

The node types and the `{{ }}` template syntax are described in the
[Workflow Engine](workflows.md#templates).

Loading is deliberately strict, because definitions can arrive over the API:

- text larger than 2 MiB is refused;
- the safe YAML loader is used, and **anchors and aliases are rejected**, so a
  few hundred bytes cannot expand into an enormous structure;
- a definition must pass `WorkflowDefinition.validate()` before it enters the
  catalog (missing `START`/`END`, dangling edges, a `CLASSIFIER` without an
  edge for a class, a nested workflow that is itself invalid, and so on);
- the id is also a file name and a URL segment, so it must match
  `[A-Za-z0-9][A-Za-z0-9_.-]{0,99}` and may not contain `..`.

One bad file in the directory is reported and skipped; it never stops the
others. Two files that declare the same id are a conflict: the first by file
name wins and the second is reported.

## HTTP API

Mounted by the `api_routers` plugin under `/v1` (and, deprecated, unprefixed)
when `WORKFLOWS_ENABLED=true`. All routes use the admin HTTP Basic credentials
except the [trigger](#inbound-trigger).

| Method and path | Purpose |
| --------------- | ------- |
| `GET /workflows` | List the catalog, cursor-paginated, ordered by id |
| `POST /workflows/validate` | Validate a JSON or YAML body without saving it |
| `POST /workflows/generate` | Draft a definition from a description (calls the model; nothing is saved) |
| `GET /workflows/{id}` | The full definition as JSON |
| `PUT /workflows/{id}` | Create or replace from a JSON or YAML body; the path decides the id |
| `DELETE /workflows/{id}` | Remove it and delete its file |
| `POST /workflows/{id}/run` | Run it: `{"input": ..., "wait": true}` |
| `GET /workflows/runs` | Recent runs, newest first (`workflow_id`, `limit`) |
| `GET /workflows/runs/{run_id}` | One run's state |
| `POST /workflows/runs/{run_id}/resume` | Continue a run paused at a `HUMAN` gate |
| `POST /workflows/{id}/trigger` | Signed inbound webhook (no Basic Auth) |

`PUT` writes `<id>.yaml` into `WORKFLOWS_DIR` and answers `persisted: true`;
without a directory the catalog is memory-only and the answer says
`persisted: false`. A body whose `id` differs from the path is refused so a typo
cannot overwrite another workflow.

A run's state is `running`, `completed`, `failed`, `timed_out` or
`awaiting_approval`:

```bash
curl -u "$ADMIN_USER:$ADMIN_PASSWORD" -X POST http://localhost:8000/v1/workflows/support-answer/run \
  -H 'Content-Type: application/json' \
  -d '{"input": {"question": "How do I reset my password?"}}'
# 200 → {"run_id": "…", "status": "completed", "output": "…", "source": "api", …}
```

- With `wait` (the default) the response carries the finished run. A run that
  stopped at a `HUMAN` gate answers **202**.
- With `"wait": false` the run starts in the background and the response is
  **202**; poll `GET /workflows/runs/{run_id}`.
- A failing node is a `failed` run with its `error`, not an HTTP error: the
  request itself succeeded.
- At most `WORKFLOWS_MAX_CONCURRENT_RUNS` runs are in flight in a process. The
  next one is **refused with 429** and `Retry-After`, not queued without bound.
- Each run is cut off after `WORKFLOWS_RUN_TIMEOUT_SECONDS` (`timed_out`).
- Run history is **process-local** and bounded by `WORKFLOWS_RUN_HISTORY_SIZE`.
  Behind several replicas, ask the replica that ran it; the checkpoint store is
  the durable record for runs that pause.

## Inbound trigger

`POST /workflows/{id}/trigger` lets an external system start a workflow. It
needs no admin credentials; the caller proves itself with an HMAC signature,
using the same scheme as the [outbound webhooks](webhooks.md):

```text
X-Baselith-Signature: t=<unix_ts>,v1=<hex HMAC-SHA256 over "<ts>.<body>">
```

- **Per-workflow keys.** `WORKFLOWS_TRIGGER_SECRET` is a master secret. Each
  workflow signs with a key *derived* from it and the workflow id, so a request
  captured for one workflow is invalid for another, and giving a partner one
  workflow's key grants nothing else. Print a key with
  `baselith workflow trigger-secret <id>`.
- **Opt-in per workflow.** Only a definition with `webhook_trigger: true` can
  be triggered. Authentication runs first, so an unauthenticated caller learns
  nothing about which workflows exist (401 either way).
- **Freshness and replay.** The timestamp must be within
  `WORKFLOWS_TRIGGER_TOLERANCE_SECONDS` (default 300), and a signature is
  accepted **once** inside that window (409 on a repeat). The replay memory is
  per process.
- **Fail closed.** Without `WORKFLOWS_TRIGGER_SECRET` the endpoint answers 503.
  Bodies over 1 MiB answer 413 before anything is hashed.
- The body becomes the workflow input: parsed JSON when it parses, otherwise
  the text. A body declared as JSON that is not JSON answers 422.

The answer is **202** with the run id, or the finished run with `?wait=true`.

```bash
BODY='{"question": "How do I reset my password?"}'
SIG=$(printf '%s' "$BODY" | baselith workflow trigger-sign support-answer)   # X-Baselith-Signature: t=…,v1=…
curl -X POST http://localhost:8000/v1/workflows/support-answer/trigger \
  -H "$SIG" -H 'Content-Type: application/json' -d "$BODY"
```

## Approval pauses

A workflow with a `HUMAN` node runs under a durable checkpoint. At the gate the
run ends as `awaiting_approval` (HTTP 202) and appears in `GET /approvals`:

1. `POST /v1/approvals/{run_id}/decision` records approve or deny.
2. `POST /v1/workflows/runs/{run_id}/resume` continues the run. Nodes that
   already finished replay from the checkpoint instead of running again; the
   gate consumes the decision and the run carries on (approved) or fails
   (denied).

!!! warning "Resume a workflow run here, not through `/approvals`"
    `POST /approvals/{run_id}/resume` re-enters the *chat orchestrator*, which
    knows nothing about catalog workflows. Record the decision with
    `/approvals`, resume with `/workflows/runs/{run_id}/resume`.

This needs a checkpoint store (`ORCHESTRATOR_CHECKPOINT_ENABLED`, on by
default). Without one the gate fails closed with a clear error, as it does for
a direct executor run. Only workflows that contain a `HUMAN` node (including
inside a `SUBGRAPH` or `ITERATION`) run under a checkpoint; every other
workflow skips that cost.

## Scheduling

A definition with a cron `schedule` fires from the catalog when
`WORKFLOWS_SCHEDULER_ENABLED=true`. Saving a workflow with a `schedule` through
the API starts it firing without a restart; removing the schedule or the
workflow stops it. Runs go through the same runtime as everything else, so they
show up in the run history with `source: schedule`, count against the
concurrency limit, and a failed run emits the workflow's `on_failure`
[webhook event](webhooks.md) when webhooks are enabled.

Enable the scheduler on **one** replica. Each process that has it enabled fires
every schedule; there is no leader election.

## MCP tools

A workflow becomes an MCP tool only when its author asks for it with
`metadata.mcp.expose: true` (see the example above). The tool is named
`workflow.<id>`, takes `{"input": …}` unless the workflow supplies its own
`input_schema` (then the argument object *is* the input), and returns the run
id, status, output and error. The catalog keeps the tool list in step as
workflows are saved and removed.

The tool's autonomy `category` defaults to `destructive`, so an exposed
workflow stays behind the MCP server's approval gate until its author states
what it does (`read_only`, `mutating`, `destructive` or
`external_side_effect`). An unknown category falls back to `destructive`.
Exposure needs the MCP server to be enabled; see [MCP](mcp.md).

## What a catalog workflow can reach

A definition uploaded through the API must not gain the reach of every
installed plugin, so the default executor is narrow:

- `TOOL` nodes can call plugin tools that declare `category: read_only`;
- `AGENT` nodes have no agents by default;
- `HTTP` nodes use only the credentials in `WORKFLOWS_HTTP_CREDENTIALS` and are
  SSRF-screened (see [HTTP node](workflows.md#http-http-request));
- `LLM`, `CLASSIFIER` and `RETRIEVE` use the framework's own LLM service and
  vector store.
- `CODE` nodes are refused unless `WORKFLOWS_CODE_ENABLED=true`, and then run in
  the sandbox, never in the framework process.

To give catalog workflows more, build the runtime yourself with an
`executor_factory` that registers the agents and tools you decide on:

```python
from baselith.core.workflows import WorkflowExecutor
from baselith.core.workflows.catalog import WorkflowCatalog
from baselith.core.workflows.runtime import WorkflowRuntime

catalog = WorkflowCatalog("./workflows")
catalog.load_directory()

runtime = WorkflowRuntime(
    catalog,
    executor_factory=lambda: WorkflowExecutor(
        agents={"analyst": my_agent},
        tools={"create_ticket": create_ticket},
    ),
)
record = await runtime.run("support-answer", {"question": "…"})
print(record.status, record.output)
```

## Security model

| Concern | Control |
| ------- | ------- |
| Who can change or run workflows | Admin HTTP Basic on every route except the trigger |
| Who can trigger | HMAC with a per-workflow derived key, fresh timestamp, single use, `webhook_trigger: true` |
| What a node can template | Data only: mapping keys and list indexes, no attribute access, no calls |
| Unresolved reference | The run fails; nothing renders a half-built prompt or request |
| Outbound HTTP | Scheme and host screening, DNS pinning, no redirects, no keep-alive, response cap, framing headers dropped |
| Secrets | Never in a definition; HTTP credentials by name from `WORKFLOWS_HTTP_CREDENTIALS`, wrapped in `SecretStr` |
| Model output steering a branch | A `CLASSIFIER` answer is accepted only if it is a declared label; put side effects behind a `HUMAN` gate |
| Resource use | Concurrency cap with 429, per-run timeout, iteration item cap, body and definition size caps |
| Plugin reach | Only `read_only` plugin tools by default |
| Code execution | `CODE` nodes off by default; when on, sandboxed (Docker or microVM) with the sandbox's ceilings |
| Drafted workflows | Validated before being shown, never saved or run by the drafter |

## CLI

`baselith workflow` works on files and needs no running server:

| Command | Purpose |
| ------- | ------- |
| `baselith workflow validate FILE…` | Check definitions; exit code 1 if any is invalid |
| `baselith workflow run FILE [--input TEXT \| --input-file PATH]` | Run once with the configured services |
| `baselith workflow export FILE [--to yaml\|json] [-o OUT]` | Convert between formats |
| `baselith workflow list [--dir DIR]` | List a catalog directory (default `WORKFLOWS_DIR`) and flag rejected files |
| `baselith workflow generate "…" [--revise FILE] [-o OUT]` | Draft a definition from a description; validated before it is shown |
| `baselith workflow trigger-secret ID` | Print the signing key of one workflow's trigger |
| `baselith workflow trigger-sign ID [--body-file PATH]` | Print the `X-Baselith-Signature` header for a body (stdin by default) |

`--format json` (global) makes every command machine-readable. `workflow run`
has no checkpoint store, so a `HUMAN` gate fails closed there.
