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
