---
title: Upgrade Notes
description: Behaviour changes an operator must know before upgrading
---
<!-- markdownlint-disable MD046 -->

The generated [CHANGELOG](https://github.com/baselithcore/baselithcore/blob/main/CHANGELOG.md)
lists *what* changed, commit by commit. This page lists the changes that alter
how a **running deployment behaves** — a default that flipped, a call that now
refuses, a signature that moved. Read it before rolling a release forward.

For the policy that governs when a change is allowed to appear here at all, see
[Versioning & Deprecation](versioning-and-deprecation.md). For the v0.3.x →
v0.4.0 module moves, see the [Migration Guide](migration-guide.md).

---

## Security postures that are now fail-closed

### MCP tokens are bound to the MCP resource (RFC 8707)

A bearer token whose `aud` claim is neither this MCP endpoint's URL nor its bare
origin is **rejected** (`401`, JSON-RPC `-32001`). A token carrying no `aud` at
all is rejected the same way. API keys are exempt — they have no issuer and no
audience to bind.

| Setting | Default | Effect |
|---|---|---|
| `MCP_REQUIRE_TOKEN_AUDIENCE` | unset | Resolves **at request time**: enforced in production, off elsewhere. `false` stands the whole check down. |
| `MCP_RESOURCE_URL` | `""` | Pins the canonical resource identifier. Unset, it is derived from `request.base_url` — i.e. from the `Host` header. |

**What can break:** `JWT_AUDIENCE` is pinned once per deployment, so the outcome
is binary. If your tokens carry an audience that is not the MCP resource URL,
*every* token mismatches and the endpoint becomes unreachable. Before upgrading a
production deployment, either request the MCP resource URL via the RFC 8707
`resource` parameter when obtaining tokens, or set `MCP_RESOURCE_URL` to the
audience your tokens already carry. `MCP_REQUIRE_TOKEN_AUDIENCE=false` is the
lever if you cannot reissue today.

See [MCP › Audience binding](../core-modules/mcp.md#audience-binding).

### Plugin admission gates refuse instead of warning

`BASELITH_ENFORCE_PLUGIN_COMPAT` and `BASELITH_ENFORCE_PLUGIN_CONFIG` are now
**on by default**. A plugin whose declared `min_core_version` /
`max_core_version` / `plugin_dependencies` are unsatisfied, or whose config
violates its own declared JSON Schema, is **skipped** rather than loaded with a
warning. Both variables survive only as explicit downgrade flags:
`false`/`0`/`no`/`off` restores warn-only while a manifest is corrected.

**What can break:** `plugin_dependencies` are part of the compat gate, so
**disabling a plugin now disables its dependents**. Turning `browser_agent` off
in `configs/plugins.yaml` skips `baselithbot` too, where it previously logged a
warning and loaded anyway. Either disable the dependents explicitly, or set the
downgrade flag for the duration.

See [Plugin System › Load-time Admission Gates](../core-modules/plugins.md#load-time-admission-gates).

### A manifest that is present but invalid is refused

The manifest schema is now `extra="forbid"`. An unknown key fails validation with
a did-you-mean hint, and the plugin is refused **in every environment**. A plugin
with *no* manifest at all still loads — that is the documented legacy shape.

The previous behaviour loaded a broken-manifest plugin with no declared
`permissions`, no `min_core_version` and no declared `environment_variables` —
strictly *more* authority than its author asked for, granted because of a typo.

See [Packaging › Manifest Fields](../plugins/packaging.md#manifest-fields).

### An approval decision must name an authenticated approver

`record_approval_decision(..., approver=None)` now raises `ValueError`, resolved
**before** the checkpoint store is touched. Pass an `ApprovalPrincipal` built
from the authenticated identity on an HTTP surface, or a bare reviewer id for an
in-process caller (recorded as `auth_method="programmatic"`). Every decision now
also emits an `AuditEventType.APPROVAL_DECISION` audit event.

See [Orchestration › Durable human-in-the-loop approvals](../core-modules/orchestration.md#durable-human-in-the-loop-approvals-pause-decide-resume).

### `AUDIT_CHAIN_REQUIRE_KEY=true` makes a bad audit config a boot failure

Setting it asks for a *tamper-evident* trail, and "boots fine, audit silently
off" is the one outcome that setting exists to prevent. With it on, an absent
`AUDIT_CHAIN_HMAC_KEY` — or any audit configuration that will not validate —
raises `AuditChainKeyError` out of `configure_audit_logging()` /
`start_audit_trail()` and stops startup. Every other audit misconfiguration still
degrades to a logged warning.

Leaving it off keeps the previous behaviour, with one loud startup warning when a
chained sink opens unkeyed.

See [Audit Trail › Tamper evidence](../core-modules/audit-trail.md#tamper-evidence).

### The indirect-injection scan of tool output is on by default

`BASELITH_INDIRECT_SCAN_TOOL_OUTPUT` changed from an opt-in to a **kill switch**.
Every tool observation is scanned and sanitized before it re-enters the context
window unless the variable is set to `0`/`false`/`no`/`off`.

Tool observations are additionally wrapped in the
[untrusted-output envelope](../core-modules/orchestration.md#untrusted-output-envelope),
and the matching system-prompt sentence (`UNTRUSTED_OUTPUT_SYSTEM_RULE`) is
appended once when an agent has tools.

!!! danger "`wrap_untrusted` is not idempotent"
    If you wrap observations yourself, call it at **exactly one seam**. There is
    deliberately no "already wrapped, return unchanged" shortcut — that shortcut
    let a crafted payload place text outside every envelope under a forged `tool=`
    attribute. Double-wrapping is the safe outcome, so calling it twice nests.

### Retrieved documents and recalled memories are enveloped

The built-in RAG handlers (`qa_docs`, streaming and not) now scan every
retrieved chunk and wrap it in the untrusted envelope, and `RAG_SYSTEM_PROMPT`
says the context is data, not instructions. `context["memory_context"]` is now
one enveloped, scanned bullet list instead of plain `- …` lines. Code that
parsed either string must strip the envelope (`unwrap_untrusted`) first; see
[Orchestration › Streaming pipeline](../core-modules/orchestration.md#streaming-pipeline).
MCP results now scan **every** text part, not only a lone text item.

### A standalone `Agent` refuses declared-destructive tools and has a budget

Outside an orchestrated request, a typed `Agent` (and so a `Crew`) now refuses
a tool explicitly declared `category="destructive"` unless it was given an
`autonomy_policy`; plain callables, tools left at the default category and
connector actions left at their default category run as before, and an `Agent`
built inside an orchestrated request (by a plugin handler) is not guarded.
Pass `autonomy_policy=None` to restore the old behaviour. A standalone run now
binds a default `LoopBudget` (the orchestrator's dollar, token and wall-clock
caps) when none is ambient; `GroupChat` and a swarm `Colony.execute_batch` bind
one **shared** budget whose iteration and tool-call counters are lifted, since
every participant's ticks land on it. Pass `loop_limits=None` (`budget=None`
for `GroupChat`) to opt out. Tools whose schema fits the strict dialect are
sent with `strict=True`. Details:
[Agent › Safe defaults for a standalone run](../core-modules/agent.md#safe-defaults-for-a-standalone-run).

### Replayed conversation history is enveloped

The prior turns the RAG prompt replays (`history_text`, rendered by
`build_rag_user_prompt`) and `context["recent_history"]` are now scanned and
sealed in one `<untrusted_tool_output tool="conversation_history">` envelope
(`render_history_context`), and `RAG_CONTEXT_IS_DATA_RULE` names the
conversation as data. Code that parsed `recent_history` must strip the
envelope (`unwrap_untrusted`) first, and must not wrap it again. See
[Orchestration › Streaming pipeline](../core-modules/orchestration.md#streaming-pipeline).

### MCP `structuredContent` is scanned

`MCPClient.call_tool()` now scans every string leaf (and key) of a tool's
`structuredContent` under the same `BASELITH_SANITIZE_EXTERNAL_CONTENT` policy
as text parts; non-string values and the object's shape are unchanged. A
payload deeper than 32 levels or wider than 10 000 containers has its
remainder scanned for detection as one block; when that is flagged under the
sanitize policy its strings are sanitized in place without recursion, and the
shape is never changed. A sanitized key that would collide with an existing
key keeps its original text, so no value is dropped. See
[MCP](../core-modules/mcp.md).

### A `Crew` run shares one crew-wide budget

With no ambient budget, `Crew.run()` now binds one shared `LoopBudget` for the
whole run (`shared_loop_limits()`: the orchestrator's dollar, token and
wall-clock caps, counters lifted), so a crew can no longer spend the default
cap once **per task**. A breach aborts the crew with `BudgetExceededError`.
`Crew(loop_limits=None)` restores the per-task default; an explicit
`LoopLimits` replaces the caps. An agent's own explicit `loop_limits` (e.g.
`LoopLimits(budget_usd=0.05)`) are still enforced for its task — and under any
ambient budget (group chat, swarm batch, orchestrated request) — through a
nested child budget that also charges the enclosing one, once. See
[Agent › Multi-agent crews](../core-modules/agent.md#multi-agent-crews-crew-task).

### Async agent runs report `retrying` between attempts

`run_agent_task` is enqueued with RQ retries (`TASK_QUEUE_DEFAULT_RETRY_COUNT`,
default `3`). An attempt that fails while retries remain now records the
non-terminal status `retrying` and emits no webhook; only the final attempt
marks `failed` and emits `agent.failed`. A poller must stop on `completed`,
`failed` or `cancelled` only. See
[Task Queue › Async Agent Runs](../core-modules/task-queue.md#async-agent-runs-agentasync).

---

## Supply chain

### Hash surface V5 — the plugin manifest is now signed

`CURRENT_HASH_SURFACE` is `V5_MANIFEST`. The digest now covers a canonical
projection of the manifest, so `permissions` (egress, tools, secrets),
`python_dependencies`, `min_core_version`, `name` and `entry_point` all move the
hash. Injecting `integrity_sha256` / `signature_ed25519` /
`hash_surface_version` after computing the digest still works — those three keys
are excluded from the projection — and comments, key order and YAML-vs-JSON
spelling remain free.

**What to do:** re-sign every plugin
(`python scripts/sign_changed_plugins.py --all`, or `baselith plugin sign <path>`
per tree). Every signed tree under `plugins/` now declares
`hash_surface_version: 5` — the nine official plugins the typing gate covers,
plus the `example-plugin` authoring reference. Editing a manifest key is now a
re-signing event, exactly like `npm run build`.

A pre-V5 signature still verifies **outside** strict mode, with a warning naming
what it does not cover. Under `BASELITH_REQUIRE_SIGNED_PLUGINS=true` it is
refused.

!!! danger "Signature enforcement alone does not close the manifest gap"
    `BASELITH_REQUIRE_PLUGIN_SIGNATURES=true` without
    `BASELITH_REQUIRE_SIGNED_PLUGINS=true` still accepts a V4-era signature — and
    a V4 signature says nothing about `permissions:`. Re-sign at V5, or enable
    both.

See [Packaging › Hash surface generations](../plugins/packaging.md#hash-surface-generations).

### Trust store: key identity, expiry and revocation

`BASELITH_PLUGIN_TRUST_STORE` points at a JSON file of
`{key_id, public_key_hex, not_after, revoked}` entries, merged with the legacy
`BASELITH_PLUGIN_TRUST_ROOTS`. A store entry for the same key **wins**, so
revoking a key takes effect even while it is still listed in the env var. A
missing, unreadable or malformed store yields no keys at all — which, with
signature enforcement on, refuses every plugin.

See [Security › Plugin trust store](security.md#plugin-trust-store).

---

## API and signature changes

### Plugin core bounds name the public core release

`min_core_version` / `max_core_version` are compared with `CORE_VERSION`
(`core/_core_version.py`) by the plugin loader, `baselith plugin add`, the plugin
update checker and the upgrade checklist — no longer with `core._version`. In the
core project the two numbers are equal, so nothing changes there. A downstream
distribution that versions its own `core/_version.py` independently must restate
its plugins' bounds as public core releases: a floor written in the
distribution's own numbering (`1.0.0` against a public `0.42.1`) now refuses the
plugin. The upgrade checklist's plugin check, previously reported as not
computed in a distribution, now runs there. `plugin_compatibility()` lost its
`framework_version`, `core_version` and `distribution` parameters.

### `POST /chat/stream` is real Server-Sent Events

It answers `text/event-stream` with one `data:` frame per model chunk, an
`event: error` frame on a mid-stream failure, and a terminal `event: done`
carrying `data: [DONE]`. It previously answered `text/plain` with the tokens
concatenated.

**What can break:** a client that read raw chunks now sees a `data:` prefix on every line.
Read *lines*, strip the field prefix and stop at `event: done`. Both first-party
SDKs and the operator console decode the framing for you; the Python and
TypeScript SDKs raise/throw `ChatStreamError` on `event: error` rather than
yielding it as text.

The stream is now also bounded by `CHAT_STREAM_TIMEOUT_SECONDS` (default
`300.0`) and stops as soon as the client disconnects, which releases the upstream
LLM call instead of leaving it running.

See [REST API › `POST /chat/stream`](../api/rest.md#post-chatstream-sse-streaming).

### SSE streams carry ids, heartbeats and a JSON error payload

`POST /chat/stream` and `GET /runs/{run_id}/events` now prefix every event
with an `id:` line and send a `: keepalive` comment every
`SSE_HEARTBEAT_SECONDS` (default `15`) of silence; the run feed also stops as
soon as the client disconnects. The chat stream's `event: error` data is now
JSON — `{"code": "stream_failed", "detail": "stream failed", "request_id": "…"}`
— instead of the bare text `stream failed`.

**What can break:** a hand-written SSE parser that treats every line as data,
or matches `data: stream failed` literally. Ignore lines starting with `:` and
unknown fields (`id:`), and read the error payload as JSON. Both SDKs (which
now expose the error's `code` and `request_id`) and the operator console
already do.

### Every API router is versioned; the unprefixed paths are deprecated

`/compliance`, `/approvals`, `/runs`, `/webhooks`, `/privacy`, `/prompts`,
`/agent` and `/api/plugins` are now also served under `/v1`, like `/chat`,
`/index`, `/reindex` and `/feedback` already were. The unprefixed copies keep
working but are `deprecated` in OpenAPI and answer with
`Deprecation: @1791072000` and `Link: </v1/…>; rel="successor-version"`.
`POST /agent/async` returns a `/v1` `status_url` and a `Location` header.
Probes, `/metrics`, `/status` and `/admin/*` are unchanged.
**Action:** move clients to `/v1/...` (the SDKs already use it). See
[REST API › API Versioning](../api/rest.md#api-versioning).

### The WebSocket chat channel is served at `/v1/chat/ws`

`/v1/chat/ws` is now the canonical path; `/chat/ws` keeps working but is
deprecated. A WebSocket handshake carries no `Deprecation` header a client
reliably sees, so the deprecation is announced in the docs only.
**Action:** point WebSocket clients at `/v1/chat/ws`. A reverse proxy that
matched `/chat/ws` exactly for its Upgrade handling must match the `/v1` path
too (the shipped nginx config routes both through `location /`).

### API responses are typed in OpenAPI

Chat, feedback, async runs, indexing, prompts, privacy, tenants, approvals,
runs and webhooks now declare response models, and their error statuses are
documented as `ProblemDetails` (`application/problem+json`). The JSON bodies
on the wire are unchanged. **Action:** none for HTTP clients; a client
generated from the OpenAPI document gets typed response classes instead of
untyped maps once regenerated.

### `POST /reindex` and `POST /admin/reindex` answer `202`

Reindexing now runs in the background: the response is `202` with
`{"status": "scheduled", "mode": "incremental", "status_url": "/v1/index/status"}`
and a `Location` header, instead of `200` with `new_files_indexed` after the
whole pass. **Action:** poll `status_url` until `running` is `false`; the count
is `last_new_documents`. `POST /index/bootstrap` likewise answers `202` with a
`status_url`.

### List endpoints are cursor-paginated

The compliance, webhooks, prompts, approvals and run-history list endpoints
accept `limit` (`1..200`, default `50`) and `cursor`, and answer `next_cursor`
and `has_more` beside the existing list key; `count` is the size of the page.
**What can break:** a client that expected the whole collection in one body —
it now gets the first 50 items; follow `next_cursor`. A `limit` above 200 on
`GET /webhooks/deliveries` is now a `422` instead of being clamped. See
[REST API › Pagination](../api/rest.md#pagination).

### Middleware refusals are problem documents; payloads are bounded

The `429` budget breach, the plugin-activation `503`, the CSRF `403` and the
idempotency `400`/`409`/`422` are now `application/problem+json` with a
stable `code` and `request_id`, instead of `{"detail": ...}` /
`{"error", "message"}`; a budget breach no longer echoes the configured
thresholds. Identifiers (`conversation_id`, `kb_label`, `tenant_id`, tenant
`id`) are capped at 128 characters, and webhook, compliance and prompt payloads
carry explicit length / item-count bounds — oversized input is a `422`.

### A2A: `/.well-known/agent-card.json` is the canonical discovery path

`/.well-known/agent.json` remains a served alias with an identical body, so
nothing breaks — but point new peers at the 0.3.0 path. The card also gained
`preferredTransport`, `securitySchemes` and `security`, and **no longer emits**
the non-spec `protocols` member (the field and its `from_dict` reading stay for
backward compatibility).

The push-notification methods were renamed to
`tasks/pushNotificationConfig/{set,get,list,delete}`; the 0.2 spellings
`tasks/pushNotification/{set,get}` are answered identically and logged as
deprecated. `TaskState` gained `auth-required` and `unknown`, both non-terminal —
deserializing a peer's task in either state no longer raises.

See [A2A Protocol](../core-modules/a2a.md).

### MCP `SessionStore` methods are async

`create`, `touch` and `terminate` are coroutines on both `SessionStore` and the
new `RedisSessionStore`; they must be awaited. The class keeps its name and its
`core.mcp.http_transport` import path. Sessions are now Redis-backed
automatically when the deployment declares `CACHE_BACKEND=redis`, which makes
them shared across replicas.

MCP capability advertisement is now gated on the negotiated protocol era: the
legacy `initialize` handshake no longer promises `listChanged` or the tasks
extension, and the modern `server/discover` no longer advertises `logging`.

See [MCP › Session storage](../core-modules/mcp.md#session-storage).

### `estimate_cost` renamed its first two parameters

`core.models.pricing.estimate_cost(model_id, input_tokens, output_tokens, ...)` —
they were `prompt_tokens` / `completion_tokens`. **Positional callers are
unaffected; keyword callers are not.** `ModelPrice.estimate` keeps the older
names.

The function also gained `cache_read_tokens`, `cache_write_tokens` and `batch`,
matching the `Usage` fields the LLM service layer produces.

See [Domain Models › Pricing](../core-modules/models.md#pricing).

### The typed `Agent` can raise two new exceptions

`Agent.run()` may now raise `BudgetExceededError` (an ambient `LoopBudget` cap
was hit — fail-closed) and `ApprovalPendingError` (a tool needs a human decision
and the run pauses durably; only reachable when the agent was given an
`autonomy_policy`). Callers that previously handled only
`AgentOutputValidationError` and `RuntimeError` should widen.

See [Agent API › What `run()` raises](../core-modules/agent.md#what-run-raises).

---

## Operations

### Production refuses to start on a stale schema (`DB_SCHEMA_CHECK`)

With `APP_ENV=production` and `DB_SCHEMA_CHECK` unset, startup now **aborts**
when the database's Alembic revision is behind the packaged head (it used to
log an error and serve). The Helm pre-upgrade migration Job and the Docker
entrypoint migrate first, so they are unaffected. A deployment that rolls out
first and migrates out of band afterwards must set `DB_SCHEMA_CHECK=warn`
**before** upgrading. A database *ahead* of the head (a rollback) only warns.
See [DB › Schema revision check](../core-modules/db.md#schema-revision-check-at-startup-db_schema_check).

### Longer Helm grace period; readiness fails while draining

`terminationGracePeriodSeconds` goes from 45 to **80** (preStop 5 s + HTTP drain
30 s + a 40 s teardown budget + 5 s margin), so a rollout that waits on old pods
takes up to 35 s longer per pod. An override below 80 cuts the teardown short:
usage sinks, the audit flush, telemetry and the connection pools no longer get
their reserved time. `/health/ready` answers `503 {"status": "draining"}` as soon
as shutdown starts. See [Runtime tuning](runtime-tuning.md).

### `queue worker --concurrency` above 1 runs a supervisor

`baselith queue worker --concurrency N` (N > 1) now runs a supervisor process that
forwards `SIGTERM` to its N children, restarts a child that crashes (with
backoff) and kills stragglers after the stop timeout. Children run in their own
process group, so a terminal `Ctrl-C` reaches them once, through the supervisor.

### PostgreSQL connections carry connect and keepalive deadlines

Every connection string now gets `connect_timeout`, TCP keepalives and
`tcp_user_timeout` (`DB_CONNECT_TIMEOUT`, `DB_TCP_*`), unless the DSN already
sets them. A failover is noticed in about a minute instead of the OS default of
up to fifteen. Set a value to `0` to hand it back to libpq and the OS.

### Metrics and spans follow the OpenTelemetry GenAI conventions

- The Prometheus label `gen_ai_system` on the `gen_ai_client_*` metrics is
  renamed **in place** to `gen_ai_provider_name`. Dashboards and alerts that
  filter or group on the old label return nothing until rewritten; a
  `label_replace` bridge is in
  [Observability › GenAI semantic conventions](../core-modules/observability-module.md#genai-semantic-conventions-genai_semconvpy).
- Spans carry `gen_ai.provider.name` (`gen_ai.system` is still emitted for one
  deprecation window), and report `aws.bedrock` / `gcp.vertex_ai` when Claude is
  served through those backends.
- Span `gen_ai.usage.input_tokens` now **includes** cached tokens, as the spec
  requires; cost accounting is unchanged.
- `gen_ai.response.finish_reason` (string) is replaced by
  `gen_ai.response.finish_reasons` (array).

---

## Configuration

### String-collection settings accept CSV, and split on a literal comma

The settings that hold a collection **of strings** — `ALLOW_ORIGINS`,
`TRUSTED_HOSTS`, `API_KEYS_*`, `OIDC_ROLE_MAP`,
`METRICS_TENANT_LABEL_ALLOWLIST` and the rest — now carry
`Annotated[..., NoDecode]` plus a `mode="before"` validator, so a plain
comma-separated value works and a **blank** value is an empty collection rather
than a `SettingsError` out of the whole settings class. Previously both raised,
which meant copying `.env.example` verbatim could break a subsystem by leaving a
key empty.

The parser (`core.config._collections.csv_list`) splits on a **literal comma**. A
value that legitimately contains one — a credential, an algorithm token, a
description — cannot be expressed in the CSV form. Both forms are accepted, so
use the JSON one there:

```bash
# Wrong: split into two useless entries
SOME_KEYS=abc,def-with,comma

# Right
SOME_KEYS=["abc", "def-with,comma"]
```

!!! warning "Nested-object settings are still JSON-only"
    Three settings hold a mapping of *objects*, not strings, and have no
    `csv_list` validator — pydantic-settings JSON-decodes them exactly as before,
    and a non-JSON value still raises `SettingsError` out of the whole class:

    | Setting | Shape |
    |---|---|
    | `MCP_SERVERS` | `dict[str, MCPServerSpec]` (`core/config/mcp.py`) |
    | `PLUGIN_PLUGIN_CONFIGS` | `dict[str, dict[str, Any]]` (`core/config/plugins.py`) |
    | `WORLD_MODEL_RISK_WEIGHTS` | `dict[str, float]` (`core/config/world_model.py`) |

    `MCP_SERVERS=weather` fails; `MCP_SERVERS='{"weather": {...}}'` is the only
    accepted form. There is no CSV spelling for a nested object, so this is a
    documented limit rather than a gap to close.

See [Configuration › Collection settings](../core-modules/config.md#collection-settings).

### Exact Claude token counting is opt-in

`BASELITH_EXACT_TOKEN_COUNTING` (default off) enables
`anthropic.messages.count_tokens` for `claude*` models. An `ANTHROPIC_API_KEY`
alone does **not** enable it: a key is present in most production deployments
already, and this call is a blocking network request.

It only ever runs from `estimate_tokens_async`, off the event loop via
`asyncio.to_thread`. The synchronous `estimate_tokens()` never makes the call
regardless of the setting — several call sites invoke it once per streamed delta.
`count_tokens_exact_available()` reports whether the setting, the SDK and the key
are all present.
The key is resolved through `LLMConfig.anthropic_api_key`, so
`LLM_ANTHROPIC_API_KEY` works as well as the bare `ANTHROPIC_API_KEY`, and a
blank value counts as unset.

### Other new switches worth knowing

| Variable | Default | Effect |
|---|---|---|
| `BASELITH_DISABLE_PLUGIN_ENTRY_POINTS` | `false` | Ignore installed distributions advertising the `baselith.plugins` entry-point group, considering only the `plugins/` directory. |
| `BASELITH_PLUGIN_TASK_CANCEL_TIMEOUT` | `10` | Seconds teardown waits for a plugin's cancelled background tasks. `0` means cancel and do not wait. |
| `MCP_TOOL_CALL_TIMEOUT_SECONDS` | `60.0` | Server-side deadline on one `tools/call`. `0` disables. |
| `MCP_MAX_TOOL_RESULT_BYTES` | `1048576` | Cap on a serialized tool result; over it, text collapses into a truncated block and `structuredContent` is dropped. `0` disables. |
| `AUDIT_CHAIN_HMAC_KEY` | unset | Keys the audit chain digest (HMAC-SHA256 instead of plain SHA-256). |
| `ORCHESTRATOR_HITL_CALLBACK_THREADS` | `8` | Width of the dedicated pool blocking human-in-the-loop callbacks run on. |

The full generated table is in
[Configuration Reference](../getting-started/configuration.md).
