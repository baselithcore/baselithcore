---
title: Client SDKs
description: Typed client libraries and OpenAPI-based code generation
---

BaselithCore ships typed first-party SDKs — **Python** (`baselith-sdk`) and
**TypeScript** (`baselith-sdk`) — and exports a complete OpenAPI schema from
which clients in any language can be generated. The SDKs wrap the REST API
documented in [REST API](rest.md) with retries, idempotency keys, streaming, and
a typed error hierarchy. Both expose the same surface: `chat`, `chat_stream`,
`submit_feedback`, `health`, `readiness`.

---

## Python SDK

### Install

```bash
pip install baselith-sdk
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
| `tenant_id`    | `None`  | Sent as `X-Tenant-ID`                          |
| `api_version`  | `"v1"`  | Path prefix; `None` calls unversioned paths   |
| `timeout`      | `30.0`  | Per-request timeout (seconds)                 |
| `max_retries`  | `2`     | Retries on 429/5xx with backoff + jitter      |
| `transport`    | `None`  | Inject an `httpx` transport (testing/proxies) |

Versioned data endpoints (`/v1/chat`, `/v1/feedback`, …) are used by default;
liveness probes (`/health`, `/health/ready`) are always called unversioned.

### Error handling

Every API failure raises a subclass of `BaselithAPIError`, each carrying
`status_code`, `code`, `message`, and `request_id` parsed from the
[error envelope](rest.md#error-envelope):

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
npm install baselith-sdk
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
`statusCode` / `code` / `requestId`; network failures throw `ApiConnectionError`.

---

## OpenAPI schema

The server exposes its schema live at `GET /openapi.json`, and the repo ships a
checked-in snapshot plus an exporter:

```bash
python scripts/export_openapi.py            # -> sdk/openapi.json
python scripts/export_openapi.py out.json   # custom path
```

The exporter only *constructs* the app (no network/DB), so it runs anywhere.
The snapshot is the source of truth used to verify the hand-written SDK against
the server contract.

### Code generation (any language)

Feed the schema to any OpenAPI generator:

```bash
# TypeScript types
npx openapi-typescript sdk/openapi.json -o client.d.ts

# Python client
openapi-python-client generate --path sdk/openapi.json
```
