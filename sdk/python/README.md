# BaselithCore Python SDK

A small, typed client for the [BaselithCore](https://baselithcore.xyz) API.
Sync and async, with retries, idempotency keys, streaming, and a typed error
hierarchy.

## Install

```bash
pip install baselith-sdk
```

## Quick start

```python
from baselith_sdk import BaselithClient

with BaselithClient("https://api.example.com", api_key="sk-...") as client:
    resp = client.chat("What is BaselithCore?")
    print(resp.answer)

    # Streaming
    for chunk in client.chat_stream("Tell me a story"):
        print(chunk, end="")

    # Feedback (idempotency key auto-generated)
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

## Authentication

Pass exactly one of:

* `api_key="sk-..."` → sent as the `x-api-key` header, or
* `bearer_token="<jwt>"` → sent as `Authorization: Bearer <jwt>` (works with
  self-issued tokens **and** federated SSO/OIDC tokens).

## Configuration

| Argument       | Default | Description                                  |
| -------------- | ------- | -------------------------------------------- |
| `base_url`     | —       | API base URL (required)                      |
| `api_key`      | `None`  | API key (`x-api-key`)                         |
| `bearer_token` | `None`  | Bearer/OIDC token                            |
| `tenant_id`    | `None`  | Sent as `X-Tenant-ID`                         |
| `api_version`  | `"v1"`  | Path prefix; `None` to call unversioned paths |
| `timeout`      | `30.0`  | Per-request timeout (seconds)                |
| `max_retries`  | `2`     | Retries on 429/5xx with backoff + jitter     |

## Errors

All API failures raise a subclass of `BaselithAPIError`
(`AuthenticationError`, `PermissionError_`, `NotFoundError`, `RateLimitError`,
`ServerError`), each carrying `status_code`, `code`, `message`, and
`request_id` parsed from the server's error envelope. Network failures raise
`APIConnectionError`.
