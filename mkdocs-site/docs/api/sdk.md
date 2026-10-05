---
title: Client SDKs
description: Typed client libraries and OpenAPI-based code generation
---

BaselithCore ships typed first-party SDKs — **Python** (`sdk/python`, package
`baselith-sdk` 0.1.0) and **TypeScript** (`sdk/typescript`, package
`baselith-sdk` 0.1.0) — and exports an OpenAPI schema from which clients in
any language can be generated. Neither package is published to PyPI or npm:
install them from the repository checkout. The SDKs wrap the REST API
documented in [REST API](rest.md) with retries, idempotency keys, streaming, and
a typed error hierarchy. Both expose the same surface — chat, streaming chat,
feedback and the probes, plus async agent runs, run event streams and history,
human-in-the-loop approvals and webhooks; see [Coverage](#coverage) for the
full method-to-route map.

## Coverage

Python names are listed; the TypeScript client uses the camelCase spelling
(`submitAgentRun`, `streamRunEvents`, …). Data routes are called under `/v1`.

| Area      | Python method                          | Route                                               | Auth             |
| --------- | -------------------------------------- | --------------------------------------------------- | ---------------- |
| Chat      | `chat`                                 | `POST /v1/chat`                                     | API key / bearer |
| Chat      | `chat_stream` (SSE → text chunks)      | `POST /v1/chat/stream`                              | API key / bearer |
| Feedback  | `submit_feedback`                      | `POST /v1/feedback`                                 | API key / bearer |
| Probes    | `health`, `readiness`                  | `GET /health`, `GET /health/ready`                  | none             |
| Async run | `submit_agent_run`                     | `POST /v1/agent/async`                              | API key / bearer |
| Async run | `get_agent_run`                        | `GET /v1/agent/status/{task_id}`                    | API key / bearer |
| Async run | `wait_for_run` (polls `get_agent_run`) | —                                                   | API key / bearer |
| Runs      | `stream_run_events` (SSE → `RunEvent`) | `GET /v1/runs/{run_id}/events`                      | admin Basic      |
| Runs      | `get_run_history` (paginated)          | `GET /v1/runs/{run_id}/history`                     | admin Basic      |
| Approvals | `list_approvals` (paginated)           | `GET /v1/approvals`                                 | admin Basic      |
| Approvals | `decide_approval`                      | `POST /v1/approvals/{run_id}/decision`              | admin Basic      |
| Approvals | `resume_run`                           | `POST /v1/approvals/{run_id}/resume`                | admin Basic      |
| Webhooks  | `create_webhook`                       | `POST /v1/webhooks`                                 | `webhooks:write` |
| Webhooks  | `list_webhooks` (paginated)            | `GET /v1/webhooks`                                  | `webhooks:read`  |
| Webhooks  | `delete_webhook`                       | `DELETE /v1/webhooks/{endpoint_id}`                 | `webhooks:write` |
| Webhooks  | `list_webhook_deliveries` (paginated)  | `GET /v1/webhooks/deliveries`                       | `webhooks:read`  |
| Webhooks  | `replay_webhook_delivery`              | `POST /v1/webhooks/deliveries/{delivery_id}/replay` | `webhooks:write` |
| Paging    | `iter_pages` (follows `next_cursor`)   | any paginated method above                          | —                |

The runs and approvals routers are mounted only with
`ORCHESTRATOR_CHECKPOINT_ENABLED`, webhooks only with `WEBHOOKS_ENABLED`; a
deployment without them answers 404. Not covered (use the
[OpenAPI schema](#openapi-schema)): run fork and state-at-version, the admin,
privacy, compliance and prompt routes, and the WebSocket chat. There is no
runs *listing* route on the server, so the SDKs have none either.

---

## Python SDK

### Install

```bash
# from the repository root
pip install ./sdk/python          # or: pip install -e ./sdk/python
```

### Quick start

```python
from baselith_sdk import BaselithClient

with BaselithClient("https://api.example.com", api_key="sk-...") as client:
    resp = client.chat("What is BaselithCore?")
    print(resp.answer)

    # Streaming — SSE framing is decoded for you; you get the model's text
    for chunk in client.chat_stream("Tell me a story"):
        print(chunk, end="")

    # Feedback — an Idempotency-Key is auto-generated for safe retries
    client.submit_feedback(
        query="What is BaselithCore?",
        answer=resp.answer,
        feedback="positive",
    )
```

### Async

```python
import asyncio
from baselith_sdk import AsyncBaselithClient

async def main():
    async with AsyncBaselithClient("https://api.example.com", api_key="sk-...") as c:
        resp = await c.chat("hello")
        print(resp.answer)
        async for chunk in c.chat_stream("stream me"):
            print(chunk, end="")

asyncio.run(main())
```

### Authentication

Pass exactly one credential:

- `api_key="sk-..."` → sent as the `x-api-key` header, or
- `bearer_token="<jwt>"` → sent as `Authorization: Bearer <jwt>`. Works with
  self-issued tokens **and** [federated SSO / OIDC](../core-modules/auth.md#federated-sso-openid-connect)
  tokens.

The runs and approvals routes sit behind the admin HTTP Basic credentials
instead: pass `basic_auth=("admin", "<password>")` (sent as
`Authorization: Basic …`). `bearer_token` and `basic_auth` both set
`Authorization`, so passing both raises `BaselithConfigError`.

### Configuration

| Argument       | Default | Description                                   |
| -------------- | ------- | --------------------------------------------- |
| `base_url`     | —       | API base URL (required)                       |
| `api_key`      | `None`  | API key (`x-api-key`)                          |
| `bearer_token` | `None`  | Bearer/OIDC token                             |
| `basic_auth`   | `None`  | `(username, password)` for HTTP Basic          |
| `tenant_id`    | `None`  | Sent as `X-Tenant-ID` (not read by the server — see below) |
| `api_version`  | `"v1"`  | Path prefix; `None` calls unversioned paths   |
| `timeout`      | `30.0`  | Per-request timeout (seconds)                 |
| `max_retries`  | `2`     | Retries on 429/5xx with backoff + jitter (see below) |
| `stream_read_timeout` | `60.0` | Max gap between SSE frames (seconds); `None` disables it |
| `transport`    | `None`  | Inject an `httpx` transport (testing/proxies) |

Retries are per call. Reads and keyed creates (`chat`, `submit_feedback`,
`submit_agent_run`, `create_webhook`, the `GET`s) are retried on transport
errors, `429` and `5xx`. **Non-idempotent calls** — `decide_approval`,
`resume_run`, `replay_webhook_delivery` — always carry an `Idempotency-Key`
(auto-generated, or pass `idempotency_key=`) and are re-sent only when the
request provably never left the client (connect error/timeout) or got a `429`.
After a read timeout or a `5xx` the handler may still be running, so the error
is raised instead: retry yourself with the **same** `idempotency_key` and the
server's idempotency layer replays the first result rather than executing twice.
`resume_run` runs the resumed agent loop inside the request, so it waits up to
`timeout=660` seconds by default (overridable per call). SSE streams
(`chat_stream`, `stream_run_events`) use `stream_read_timeout` — the longest
silence allowed between two frames — instead of `timeout`, so a quiet stream
kept alive by heartbeats is not cut at 30 s.

Versioned data endpoints (`/v1/chat`, `/v1/feedback`, …) are used by default;
liveness probes (`/health`, `/health/ready`) are always called unversioned.

!!! note "`tenant_id` is informational"
    The server derives the tenant from the **authenticated identity**
    (`core/middleware/tenant.py`) and never reads `X-Tenant-ID`; the header
    only helps proxies and logs. The `tenant_id` field of the chat body
    (`ChatRequest.tenant_id`) is accepted for compatibility but is not read
    by the chat route either — there is no request-level tenant override.

### Error handling

Every API failure raises a subclass of `BaselithAPIError`, each carrying
`status_code`, `code`, `message`, `error_type`, `request_id` and the raw
`body`, parsed from the server's RFC 9457
[problem document](rest.md#error-envelope):

- `code` — the stable `code` member (e.g. `not_found`, `rate_limited`)
- `message` — `detail`, falling back to `title`
- `error_type` — the `type` URN (`urn:baselith:error:<code>`)
- `request_id` — the `request_id` member, else the `X-Request-ID` header

The legacy `{"error": {...}}` envelope and FastAPI's bare `{"detail": ...}`
shape are still recognised for older servers. Both clients decode the body for
`application/json` **and** any `+json` media type — earlier releases matched
only `application/json`, so the server's `application/problem+json` errors
were left as raw text and `code` / `request_id` came back empty.

| Exception             | When                                |
| --------------------- | ----------------------------------- |
| `AuthenticationError` | 401 — missing/invalid credentials   |
| `PermissionError_`    | 403 — missing role or scope         |
| `NotFoundError`       | 404                                 |
| `RateLimitError`      | 429 (carries `retry_after`)         |
| `ServerError`         | 5xx                                 |
| `APIConnectionError`  | network failure or timeout          |

```python
from baselith_sdk import BaselithClient, RateLimitError, AuthenticationError

try:
    client.chat("hi")
except RateLimitError as e:
    print("slow down; retry after", e.retry_after)
except AuthenticationError as e:
    print("bad credentials", e.request_id)
```

### Streaming errors

`POST /chat/stream` is [Server-Sent Events](rest.md#post-chatstream-sse-streaming),
and both clients decode the wire format for you: `data:` frames become text
chunks, multi-line frames are rejoined, `: keepalive` comments are ignored, and
`event: done` ends the iteration. A stream that fails *after* the 200 headers
went out carries `event: error` instead — which the clients raise rather than
yield, so a failure can never masquerade as model output:

```python
from baselith_sdk import BaselithClient, ChatStreamError

try:
    for chunk in client.chat_stream("Tell me a story"):
        print(chunk, end="")
except ChatStreamError as e:
    print("\nstream failed:", e, e.code, e.request_id)
```

`ChatStreamError` subclasses `BaselithError`, so an `except BaselithError`
already catches it. Current servers send the error as JSON; the client exposes
its `code` (`"stream_failed"`) and `request_id` — the id to quote when
reporting the failure — and still accepts the bare `stream failed` text older
servers sent (both attributes are then `None`). `id:` fields are ignored on the
chat stream (the run event stream below reads them). The TypeScript client throws its own `ChatStreamError`
(exported from `baselith-sdk`) in the same situation:

```typescript
import { BaselithClient, ChatStreamError } from "baselith-sdk";

try {
  for await (const chunk of client.chatStream("Tell me a story")) {
    process.stdout.write(chunk);
  }
} catch (err) {
  if (err instanceof ChatStreamError) console.error("stream failed:", err.message, err.code, err.requestId);
  else throw err;
}
```

### Async runs, run events, approvals and webhooks

```python
from baselith_sdk import BaselithClient, RunTimeoutError

with BaselithClient("https://api.example.com", api_key="sk-...") as client:
    sub = client.submit_agent_run("Summarise the Q3 report")  # 202: task_id + status_url
    try:
        status = client.wait_for_run(sub.task_id, timeout=120, poll_interval=2)
        print(status.status, status.result)        # completed / failed / cancelled
    except RunTimeoutError as e:
        print("still running:", e.last_status)

    # Every list endpoint is cursor-paginated: one page, or all of them
    first = client.list_webhooks(limit=50)
    for page in client.iter_pages(client.list_webhook_deliveries, limit=100):
        for delivery in page.items:
            print(delivery.id, delivery.status)

    created = client.create_webhook("https://hooks.example.com/baselith",
                                    event_types=["agent.completed", "agent.failed"])
    print(created.secret)  # returned once — store it to verify signatures

with BaselithClient("https://api.example.com", basic_auth=("admin", "pw")) as ops:
    for page in ops.iter_pages(ops.list_approvals):
        for pending in page.items:
            ops.decide_approval(pending.run_id, approved=True, reason="reviewed")
            ops.resume_run(pending.run_id)

    # Structured events (SSE): subscribe before starting/resuming the run
    for event in ops.stream_run_events("run-123"):
        print(event.id, event.type, event.content)  # ends after final/error/human
```

`wait_for_run` polls `get_agent_run` until the status is `completed`,
`failed` or `cancelled` and raises `RunTimeoutError` (carrying `last_status`)
once `timeout` seconds pass. `stream_run_events` reuses the chat stream's SSE
decoder — keepalive comments are skipped, multi-line `data:` is rejoined — but
reads each frame's `id:` into `RunEvent.id`, yields `event: error` as an
ordinary terminal event rather than raising, and stops after the terminal
`final` / `error` / `human` event. The feed is fan-out only: the
`last_event_id` argument is sent as `Last-Event-ID` but cannot rewind it, so
catch up from `get_run_history` after a reconnect. Pages expose `items`,
`next_cursor` and `has_more`; `iter_pages(fetch, *args, **kwargs)` (also a
module-level function, with `aiter_pages` for the async client) passes the
cursor back until `has_more` is false. Path parameters are percent-encoded, so
an id containing `/` cannot reach a different route.

---

## TypeScript SDK

Zero runtime dependencies (built on the platform `fetch`); runs in Node 18+,
browsers, and edge runtimes.

### Install

```bash
# build from the repository checkout, then install the folder into your project
cd sdk/typescript && npm install && npm run build
npm install /path/to/baselithcore/sdk/typescript
```

### Quick start

```ts
import { BaselithClient } from "baselith-sdk";

const client = new BaselithClient({
  baseUrl: "https://api.example.com",
  apiKey: "sk-...",
});

const res = await client.chat("What is BaselithCore?");
console.log(res.answer);

for await (const chunk of client.chatStream("Tell me a story")) {
  process.stdout.write(chunk);
}

await client.submitFeedback({
  query: "What is BaselithCore?",
  answer: res.answer,
  feedback: "positive",
});

// Async runs
const sub = await client.submitAgentRun("Summarise the Q3 report");
const status = await client.waitForRun(sub.task_id, { timeoutMs: 120_000, pollIntervalMs: 2_000 });

// Pagination
for await (const page of client.iterPages((o) => client.listWebhookDeliveries(o), { limit: 100 })) {
  for (const d of page.deliveries) console.log(d.id, d.status);
}

// Approvals + run events (admin Basic credentials)
const ops = new BaselithClient({
  baseUrl: "https://api.example.com",
  basicAuth: { username: "admin", password: "pw" },
});
for await (const event of ops.streamRunEvents("run-123")) console.log(event.id, event.type);
```

### Authentication & errors

Pass `apiKey` (`x-api-key`) or `bearerToken` (`Authorization: Bearer`, works with
[OIDC SSO](../core-modules/auth.md#federated-sso-openid-connect) tokens), and
`basicAuth: { username, password }` for the admin-protected runs and approvals
routes (combining it with `bearerToken` throws `BaselithConfigError`). Failures
throw a subclass of `BaselithApiError` (`AuthenticationError`,
`PermissionDeniedError`, `NotFoundError`, `RateLimitError`, `ServerError`) with
`statusCode` / `code` / `errorType` / `requestId` / `body`, parsed from the
same RFC 9457 problem document as the Python SDK (`message` = `detail`
falling back to `title`, `errorType` = `type`); network failures throw
`ApiConnectionError`; `waitForRun` throws `RunTimeoutError` (with `taskId`,
`timeoutMs`, `lastStatus`).

The retry policy matches the Python client: `decideApproval`, `resumeRun` and
`replayWebhookDelivery` always send an `Idempotency-Key` (`{ idempotencyKey }`
to choose it) and are re-sent only on a `429` or when
`ApiConnectionError.notSent` is true (connection refused, DNS failure, connect
timeout) — never after a timeout or a `5xx`. `resumeRun` waits up to
`timeoutMs: 660_000` by default. `timeoutMs` bounds only the wait for response
headers, so streams are not cut by it, and breaking out of `chatStream` /
`streamRunEvents` cancels the response body so the connection is released.

---

## OpenAPI schema

The server exposes its schema live at `GET /openapi.json` (while docs are
enabled), and the repo ships a checked-in snapshot plus an exporter:

```bash
python scripts/export_openapi.py            # -> sdk/openapi.json
python scripts/export_openapi.py out.json   # custom path
```

The exporter *constructs* the app (no network/DB connections are opened) and
then mounts the routers the `api_routers` plugin adds at startup (`/prompts`,
`/chat/ws`, `/agent/*`, `/webhooks`, `/privacy`, `/compliance`, `/runs`,
`/approvals`) with every feature gate opened, so `sdk/openapi.json` describes
the full server surface rather than the subset a bare `create_app()` exposes.
It runs anywhere and its output is deterministic, which is what the
`openapi_drift` CI job diffs against. A running deployment still serves only
the routers **its** flags enable.

### Code generation (any language)

Feed the schema to any OpenAPI generator:

```bash
# TypeScript types
npx openapi-typescript sdk/openapi.json -o client.d.ts

# Python client
openapi-python-client generate --path sdk/openapi.json
```

### The clients stay in step with the schema

The two clients are hand-written and cover a deliberate subset — the routes in
the [Coverage](#coverage) table — not the full route surface.
`scripts/check_sdk_contract.py` keeps that subset honest:

```bash
python scripts/check_sdk_contract.py --list   # what each client calls
python scripts/check_sdk_contract.py          # the gate
```

It parses the routes out of the client sources (Python via AST, TypeScript via
the request helpers) — routes with parameters are written as their OpenAPI
templates (`/agent/status/{task_id}`) and filled in by the transport, which is
what lets the gate match them — and fails when a client calls a `(method, path)` the
committed schema does not declare, or when the two clients stop calling the
same set. The `openapi_drift` job keeps `sdk/openapi.json` in step with the app;
this closes the other half, so a renamed route can no longer reach a consumer's
application before it reaches CI.
