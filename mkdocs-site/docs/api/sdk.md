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
a typed error hierarchy. Both expose the same surface: `chat`, `chat_stream`,
`submit_feedback`, `health`, `readiness`.

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

    # Streaming (raw text chunks)
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

### Configuration

| Argument       | Default | Description                                   |
| -------------- | ------- | --------------------------------------------- |
| `base_url`     | —       | API base URL (required)                       |
| `api_key`      | `None`  | API key (`x-api-key`)                          |
| `bearer_token` | `None`  | Bearer/OIDC token                             |
| `tenant_id`    | `None`  | Sent as `X-Tenant-ID` (not read by the server — see below) |
| `api_version`  | `"v1"`  | Path prefix; `None` calls unversioned paths   |
| `timeout`      | `30.0`  | Per-request timeout (seconds)                 |
| `max_retries`  | `2`     | Retries on 429/5xx with backoff + jitter      |
| `transport`    | `None`  | Inject an `httpx` transport (testing/proxies) |

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
shape are still recognised for older servers.

| Exception             | When                                |
| --------------------- | ----------------------------------- |
| `AuthenticationError` | 401 — missing/invalid credentials   |
| `PermissionError_`    | 403 — missing role or scope         |
| `NotFoundError`       | 404                                 |
| `RateLimitError`      | 429 (carries `retry_after`)         |
| `ServerError`         | 5xx                                 |
| `APIConnectionError`  | network/timeout (request never sent)|

```python
from baselith_sdk import BaselithClient, RateLimitError, AuthenticationError

try:
    client.chat("hi")
except RateLimitError as e:
    print("slow down; retry after", e.retry_after)
except AuthenticationError as e:
    print("bad credentials", e.request_id)
```

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
```

### Authentication & errors

Pass `apiKey` (`x-api-key`) or `bearerToken` (`Authorization: Bearer`, works with
[OIDC SSO](../core-modules/auth.md#federated-sso-openid-connect) tokens). Failures
throw a subclass of `BaselithApiError` (`AuthenticationError`,
`PermissionDeniedError`, `NotFoundError`, `RateLimitError`, `ServerError`) with
`statusCode` / `code` / `errorType` / `requestId` / `body`, parsed from the
same RFC 9457 problem document as the Python SDK (`message` = `detail`
falling back to `title`, `errorType` = `type`); network failures throw
`ApiConnectionError`.

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

The two clients are hand-written and cover a deliberate subset — chat,
streaming chat, feedback, health, readiness — not the full route surface.
`scripts/check_sdk_contract.py` keeps that subset honest:

```bash
python scripts/check_sdk_contract.py --list   # what each client calls
python scripts/check_sdk_contract.py          # the gate
```

It parses the routes out of the client sources (Python via AST, TypeScript via
the request helpers) and fails when a client calls a `(method, path)` the
committed schema does not declare, or when the two clients stop calling the
same set. The `openapi_drift` job keeps `sdk/openapi.json` in step with the app;
this closes the other half, so a renamed route can no longer reach a consumer's
application before it reaches CI.
