---
title: Human-in-the-Loop
description: Human intervention for critical decisions and approvals
---

**Module**: `core/human/`

The Human-in-the-Loop (HITL) module lets an agent pause autonomous execution to
ask a human for approval, input, a selection, or to send a notification. This is
essential for enterprise safety before sensitive actions (destructive API calls,
financial transactions, production deployments).

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
callback may be sync or async; if it is async and the request carries a
`timeout`, the wait is bounded by `asyncio.wait_for`. When **no callback** is
registered, every request is auto-rejected (returns `False`/`None`/`""`).

```python
from core.human import HumanIntervention, HumanRequest

async def ui_callback(request: HumanRequest):
    # Forward `request` to your UI/chat, await the operator, return the answer.
    # APPROVAL -> return a truthy/falsy value; INPUT/SELECTION -> return a string.
    ...

intervention = HumanIntervention(callback=ui_callback)
```

`InteractionType`: `APPROVAL`, `INPUT`, `SELECTION`, `NOTIFICATION`.
`InteractionStatus`: `PENDING`, `APPROVED`, `REJECTED`, `COMPLETED`, `TIMEOUT`.

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

---

## Inspecting Pending Requests

While a request is awaiting a response it lives in an internal registry:

```python
intervention.has_pending_requests()   # bool
intervention.get_pending_requests()   # list[HumanRequest]
```

Each request is removed from the registry once it resolves (completed, rejected,
or timed out).

!!! info "Behavior without an interface"
    `HumanIntervention` does not block forever waiting for a human. If no
    callback is wired, requests are immediately auto-rejected and logged — so an
    agent never deadlocks when run headless.
