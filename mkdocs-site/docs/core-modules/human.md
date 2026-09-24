---
title: Human-in-the-Loop
description: Human intervention for critical decisions and approvals
---
<!-- markdownlint-disable MD046 -->

**Module**: `core/human/`

The Human-in-the-Loop (HITL) module lets an agent pause autonomous execution to
ask a human for approval, input, a selection, or to send a notification. This is
essential for enterprise safety before sensitive actions (destructive API calls,
financial transactions, production deployments).

!!! note "Library API — not wired by default"
    The default app never constructs a `HumanIntervention`: the orchestrator
    the chat service builds is created without `human_intervention=`, so
    nothing on the request path calls it. Approval pauses in the running app
    go through the durable checkpoint path and the `/approvals` API instead
    (see
    [Orchestration › Durable approvals](orchestration.md#durable-human-in-the-loop-approvals-pause-decide-resume)).
    Pass `human_intervention=HumanIntervention(callback=...)` when you build an
    `Orchestrator` in host code.

---

## Module Structure

```text
core/human/
├── __init__.py       # Public exports
└── interaction.py    # HumanIntervention + request/enums
```

Public exports:

```python
from core.human import (
    HumanIntervention,
    HumanRequest,
    InteractionType,
    InteractionStatus,
)
```

---

## Core Concepts

`HumanIntervention` is the manager. It is constructed with an optional
**callback** that connects requests to a real interface (UI, CLI, chat). The
callback may be sync or async, and both shapes behave alike: a single
`asyncio.wait_for(..., timeout=request.timeout_seconds)` bounds the wait either
way. When **no callback** is registered, every request is auto-rejected
(returns `False`/`None`/`""`).

!!! info "A synchronous callback runs on a dedicated pool"
    A blocking prompt (`input()`, a modal dialog, a queue `get`) is run through
    `core.human.executor.run_hitl_callback` rather than on the loop, and on a
    pool of its own rather than the interpreter default one — which is only
    `cpu_count + 4` wide and shared with SSRF DNS resolution, audit appends and
    tokenization. A handful of pending approvals would otherwise leave those
    short, latency-critical tasks queued behind a human. Width:
    `ORCHESTRATOR_HITL_CALLBACK_THREADS`, default `8`.

    A callback that outlives its timeout cannot be cancelled: its thread runs to
    completion and the result is discarded, while the caller is released on time.
    That is why the pool is sized for humans and separate from everything else.
    A callable whose *return value* is awaitable (an async `__call__`, a lambda
    wrapping a coroutine function) is awaited too.

```python
from core.human import HumanIntervention, HumanRequest

async def ui_callback(request: HumanRequest):
    # Forward `request` to your UI/chat, await the operator, return the answer.
    # APPROVAL -> return a truthy/falsy value; INPUT/SELECTION -> return a string.
    ...

intervention = HumanIntervention(callback=ui_callback)
```

`InteractionType`: `APPROVAL`, `INPUT`, `SELECTION`, `NOTIFICATION`.

`InteractionStatus`:

| Status | Meaning |
| --- | --- |
| `PENDING` | Awaiting a human response. |
| `COMPLETED` | A human answered — **including an APPROVAL that was denied**. The decision itself is in `request.response`, not in the status. |
| `REJECTED` | No human ever saw it: no interface was connected, or the callback raised. |
| `TIMEOUT` | `timeout_seconds` elapsed with no response. |
| `CANCELLED` | The awaiting caller was cancelled before a human answered. Archived as terminal so it never reads as "a human is still looking at it"; the cancellation is re-raised, never swallowed. |
| `APPROVED` | Reserved; the approval path resolves to `COMPLETED`. |

A `HumanRequest` is a dataclass with `type`, `description`, auto-generated `id`
(`uuid4`), `data`, `options`, `timeout_seconds`, `created_at`, `status`, and
`response`.

---

## Requesting Approval

`request_approval(action_description, timeout=None, context=None) -> bool`
returns `True` only when approved; rejection, timeout, or a missing callback all
yield `False`.

```python
approved = await intervention.request_approval(
    "Send email to 1000 users?",
    timeout=60,
    context={"template": "newsletter"},
)

if approved:
    await execute_action()
else:
    log.info("Action not approved by human")
```

## Asking for Input

`ask_input(question, timeout=None, context=None) -> str` returns the human's
text, or an empty string if there is no response.

```python
api_key = await intervention.ask_input(
    "Please provide the API key:",
    timeout=120,
)
```

## Requesting a Selection

`request_selection(prompt, options, timeout=None, context=None) -> str | None`
returns the chosen option, or `None` if nothing valid was selected. The response
is validated against `options`.

```python
env = await intervention.request_selection(
    "Choose deployment target:",
    options=["staging", "production"],
    timeout=30,
)
```

## Notifying

`notify(message, context=None) -> None` is fire-and-forget — no response is
expected.

```python
await intervention.notify(
    "Background task completed successfully",
    context={"task_id": "abc123", "duration_ms": 5000},
)
```

The engineered-loop default escalation sink delivers its hand-off through
this channel — see
[Loop Engineering › Default escalation](loops.md#default-escalation-escalationpy).

---

## Inspecting Requests

While a request is awaiting a response it lives in the pending registry; once it
resolves it moves to a bounded archive, so an outcome can still be read back
after the `await` returns:

```python
intervention.has_pending_requests()            # bool — in flight only
intervention.get_pending_requests()            # list[HumanRequest] — in flight only

intervention.get_recent_requests()             # list[HumanRequest] — terminal, newest last
intervention.get_recent_requests(limit=20)
intervention.get_recent_requests(status=InteractionStatus.TIMEOUT)

intervention.get_request(request_id)           # HumanRequest | None — either registry
```

The archive is insertion-ordered and bounded: `DEFAULT_MAX_RECENT_REQUESTS` is
`256`, overridable per manager with `HumanIntervention(..., max_recent=N)`, and
the oldest entry is evicted first.

!!! warning "Retained requests carry no tenant tag"
    A `HumanRequest` records `type`, `description`, `data`, `options`, `status`
    and `response` — and nothing about which tenant or user it belongs to. The
    archive is a process-wide list, so in a multi-tenant deployment
    `get_recent_requests()` cannot be filtered by tenant and its contents must not
    be exposed on a tenant-scoped surface. Put whatever attribution you need into
    `context`/`data` at request time and filter on it yourself.

!!! info "Behavior without an interface"
    `HumanIntervention` does not block forever waiting for a human. If no
    callback is wired, requests are immediately auto-rejected and logged — so an
    agent never deadlocks when run headless.
