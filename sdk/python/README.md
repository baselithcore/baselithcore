# BaselithCore Python SDK

A small, typed client for the [BaselithCore](https://baselithcore.xyz) API.
Sync and async, with retries, idempotency keys, streaming, cursor pagination,
and a typed error hierarchy. Covers chat, streaming chat, feedback, health and
readiness, async agent runs, run event streams and history, human-in-the-loop
approvals, and webhooks.

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

### Async agent runs

```python
from baselith_sdk import RunTimeoutError

sub = client.submit_agent_run("Summarise the Q3 report")   # POST /v1/agent/async
print(sub.task_id, sub.status_url)
try:
    status = client.wait_for_run(sub.task_id, timeout=120, poll_interval=2)
    print(status.status, status.result)   # completed / failed / cancelled
except RunTimeoutError as e:
    print("still running:", e.last_status)
```

`get_agent_run(task_id)` reads the status once; `wait_for_run` polls it until
a terminal state.

### Run events, history and approvals

These routes take the admin HTTP Basic credentials:

```python
with BaselithClient("https://api.example.com", basic_auth=("admin", "pw")) as ops:
    # Structured agent events over SSE; ends after a final/error/human event.
    for event in ops.stream_run_events("run-123"):
        print(event.id, event.type, event.content)

    history = ops.get_run_history("run-123", limit=20)

    for page in ops.iter_pages(ops.list_approvals):
        for pending in page.items:
            ops.decide_approval(pending.run_id, approved=True, reason="reviewed")
            ops.resume_run(pending.run_id)
```

Subscribe to the event stream before starting or resuming the run — the feed
is not replayed (`last_event_id=` is sent but cannot rewind it).

### Webhooks

```python
created = client.create_webhook(
    "https://hooks.example.com/baselith",
    event_types=["agent.completed", "agent.failed"],
)
print(created.secret)            # returned only once
client.list_webhooks(limit=50)
client.delete_webhook(created.endpoint.id)
for page in client.iter_pages(client.list_webhook_deliveries, limit=100):
    for delivery in page.items:
        if delivery.status == "failed":
            client.replay_webhook_delivery(delivery.id)
```

### Pagination

List methods return one page (`items`, `next_cursor`, `has_more`) and take
`limit=` / `cursor=`. `client.iter_pages(method, *args, **kwargs)` follows
`next_cursor` to the end; on `AsyncBaselithClient` it is an async iterator
(`async for page in client.iter_pages(client.list_webhooks)`).

## Authentication

Pass exactly one of:

* `api_key="sk-..."` → sent as the `x-api-key` header, or
* `bearer_token="<jwt>"` → sent as `Authorization: Bearer <jwt>` (works with
  self-issued tokens **and** federated SSO/OIDC tokens).

The runs and approvals routes use the admin HTTP Basic credentials instead:
`basic_auth=("admin", "<password>")`. It cannot be combined with
`bearer_token` (both set `Authorization`).

## Configuration

| Argument       | Default | Description                                  |
| -------------- | ------- | -------------------------------------------- |
| `base_url`     | —       | API base URL (required)                      |
| `api_key`      | `None`  | API key (`x-api-key`)                         |
| `bearer_token` | `None`  | Bearer/OIDC token                            |
| `basic_auth`   | `None`  | `(username, password)` for HTTP Basic         |
| `tenant_id`    | `None`  | Sent as `X-Tenant-ID`                         |
| `api_version`  | `"v1"`  | Path prefix; `None` to call unversioned paths |
| `timeout`      | `30.0`  | Per-request timeout (seconds)                |
| `max_retries`  | `2`     | Retries on 429/5xx with backoff + jitter     |
| `stream_read_timeout` | `60.0` | Max gap between SSE frames; `None` disables it |

`decide_approval`, `resume_run` and `replay_webhook_delivery` are
non-idempotent: they always send an `Idempotency-Key` (pass
`idempotency_key=` to choose it) and are retried only when the request never
left the client (connect error) or got a `429` — never after a read timeout or
a `5xx`, when the server may still be executing them. Retry those yourself with
the same key. `resume_run` runs the resumed agent loop in-request and waits up
to `timeout=660` seconds by default.

## Errors

All API failures raise a subclass of `BaselithAPIError`
(`AuthenticationError`, `PermissionError_`, `NotFoundError`, `RateLimitError`,
`ServerError`), each carrying `status_code`, `code`, `message`, and
`request_id` parsed from the server's error envelope. Network failures raise
`APIConnectionError`; `wait_for_run` raises `RunTimeoutError`.
