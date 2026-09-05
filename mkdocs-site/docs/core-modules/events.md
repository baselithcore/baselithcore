---
title: Event System
description: Internal event bus for pub/sub communication
---

The `core/events` module provides an **event bus** for asynchronous and decoupled communication between system components.

## What is the Pub/Sub Pattern

The **Publish-Subscribe** (Pub/Sub) pattern is an asynchronous messaging paradigm where:

- **Publisher**: Emits events without knowing who will receive them
- **Subscriber**: Registers to receive specific event types
- **Event Bus**: Manages distribution of events to interested subscribers

This approach eliminates direct coupling between components, allowing for a more modular and maintainable architecture.

### Decoupling Benefits

**Modularity**: Components don't need to know each other - a plugin can emit events without knowing who listens to them.

**Extensibility**: New subscribers can be added without modifying existing publishers.

**Observability**: Centralizes logging, monitoring, and auditing by listening to system events.

**Resilience**: If a subscriber fails, it doesn't impact the publisher or other subscribers.

### When to Use Events vs Direct Calls

| Scenario                | Recommended Solution | Rationale                                  |
| ----------------------- | -------------------- | ------------------------------------------ |
| Broadcast notifications | **Events**           | A component must inform multiple listeners |
| Audit logging           | **Events**           | Track operations without coupling logic    |
| Modular pipelines       | **Events**           | Allow extensions without modifying core    |
| Request-Response        | **Direct Call**      | Immediate and specific response required   |
| Critical operations     | **Direct Call**      | Deterministic error handling required      |
| Performance critical    | **Direct Call**      | Minimal latency required                   |

!!! tip "Best Practice"
    Use events for **notifications** and **side-effects**, direct calls for **business logic** where a response is required.

---

## Structure

```text
core/events/
├── __init__.py
├── _singleton.py     # get_event_bus() / reset_event_bus() global accessor
├── bus.py            # EventBus
├── listener.py       # EventListener (aggregated metrics)
├── names.py          # EventNames constants
├── types.py          # Event, EventStats, handler type aliases
└── validation.py     # EventSchemaRegistry, DeadLetterQueue
```

---

## Event Bus

Asynchronous Pub/Sub:

```python
from core.events import get_event_bus, EventNames

bus = get_event_bus()

# Subscribe — decorator form; higher priority runs first (default 0)
@bus.on(EventNames.FLOW_COMPLETED, priority=10)
async def on_flow_complete(data: dict):
    print(f"Flow {data['intent']} completed in {data['duration_ms']}ms")

# Subscribe — functional form; returns an unsubscribe callable
unsubscribe = bus.subscribe(EventNames.FLOW_COMPLETED, on_flow_complete)
unsubscribe()

# Emit — returns the number of handlers invoked
await bus.emit(
    EventNames.FLOW_COMPLETED,
    {"intent": "weather", "duration_ms": 150, "success": True},
    source="orchestrator",       # optional; stored on the Event record
    correlation_id="req-42",     # optional; for tracing related events
)
```

Handlers may be `async` or plain functions (sync handlers run in the default
executor). Wildcard subscriptions — `"agent.*"`, `"*.failed"`, `"*"` — work
while `EVENT_ENABLE_WILDCARDS` is on.

### Delivery semantics

`emit()` resolves the matching handlers, appends the `Event` to the history
ring-buffer, and then runs **every handler concurrently** with
`asyncio.gather(..., return_exceptions=True)`. There is no internal queue:

- With the default `wait=True`, the `await` returns once all handlers have
  finished — or been cancelled by `EVENT_HANDLER_TIMEOUT`. A handler that
  raises is logged (and captured in the DLQ when enabled) but never
  propagates to the emitter or to the other handlers.
- With `wait=False`, each handler coroutine is wrapped in a tracked
  background task and `emit()` returns immediately.

`emit_sync(name, data, **kwargs)` is the escape hatch for non-async code:
inside a running loop it schedules `emit()` as a tracked task and returns `0`;
with no loop it runs `asyncio.run(emit(...))`. The orchestrator emits
`FLOW_COMPLETED` this way.

### History, dead-letter queue and schemas

- **History** — `bus.get_history(event_name=None, limit=10)` returns the most
  recent `Event` records (`name`, `data`, `timestamp`, `source`,
  `correlation_id`) from a ring-buffer sized by `EVENT_MAX_HISTORY`;
  `bus.stats` exposes `events_published`, `events_handled`,
  `handlers_registered` and `errors`.
- **Dead-letter queue** — with `EVENT_ENABLE_DLQ=true` a handler that raises
  or times out is captured as a `DeadLetterEntry` (`event_name`, `data`,
  `error`, `handler_name`, `timestamp`, `retry_count`) in a
  `DeadLetterQueue` bounded by `EVENT_DLQ_MAX_SIZE`. Reach it via
  `bus.dead_letter_queue` or the process-wide `get_dead_letter_queue()`;
  inspect with `get_all()` / `get_by_event(name)` / `stats()`, drain with
  `pop()` / `clear()`. There is no built-in retry — replaying an entry is
  the caller's decision.
- **Schemas** — `EventSchemaRegistry` (`get_schema_registry()`, attached as
  `bus.schema_registry` when `EVENT_ENABLE_VALIDATION=true`) maps event names
  to Pydantic models: `register(event_name, schema)`,
  `validate(event_name, data) -> (is_valid, error)`, `has_schema`,
  `get_schema`, `registered_events`. When the registry is attached, `emit()`
  validates every payload that has a registered schema and raises
  `EventValidationError` (no handler runs) on a mismatch; events without a
  schema pass through untouched.

---

## Practical Scenarios

Here are some concrete use cases where events improve architecture.

### Automatic Audit Trail

Automatically track all important operations without modifying business logic:

```python
from core.events import get_event_bus, EventNames

bus = get_event_bus()

# Subscriber for audit
@bus.on(EventNames.FLOW_COMPLETED)
async def audit_logger(data: dict):
    await db.insert_audit_log(
        action="flow_completed",
        intent=data["intent"],
        duration_ms=data["duration_ms"],
        timestamp=datetime.utcnow()
    )

# Subscribe to the concrete failure events (there is no generic ERROR event)
@bus.on(EventNames.AGENT_FAILED)
@bus.on(EventNames.TASK_FAILED)
@bus.on(EventNames.EVALUATION_FAILED)
async def audit_error(data: dict):
    await db.insert_audit_log(
        action="error",
        source=data.get("source"),
        error=data.get("error"),
        severity="high"
    )
```

**Benefit**: Audit logging is completely separated from business logic. You can enable/disable it without touching handler code.

### Metrics and Monitoring

Collect real-time metrics by listening to system events:

```python
from core.events import get_event_bus
import prometheus_client as prom

bus = get_event_bus()

# Prometheus Metrics
flow_duration = prom.Histogram('flow_duration_seconds', 'Flow execution time')
flow_counter = prom.Counter('flows_total', 'Total flows', ['intent', 'status'])

@bus.on(EventNames.FLOW_COMPLETED)
async def record_metrics(data: dict):
    flow_duration.observe(data['duration_ms'] / 1000)
    status = 'success' if data['success'] else 'failure'
    flow_counter.labels(intent=data['intent'], status=status).inc()
```

### Plugin Communication

Plugins can communicate with each other without knowing each other:

```python
# Plugin A: Research Plugin
await bus.emit("research.completed", {
    "query": query,
    "results": research_data,
    "confidence": 0.92
})

# Plugin B: Report Generator (auto-trigger)
@bus.on("research.completed")
async def auto_generate_report(data):
    if data["confidence"] > 0.9:
        await generate_report(data["results"])
```

**Benefit**: The Research plugin doesn't know about the Report Generator, maintaining decoupling.

---

## Standard Events

| Event                  | Emitter           | Data                         |
| ---------------------- | ----------------- | ---------------------------- |
| `FLOW_STARTED`         | Orchestrator      | intent, query                |
| `FLOW_COMPLETED`       | Orchestrator      | intent, query, response, context, duration_ms, success, run_id — on failure: intent, duration_ms, success=False, error, run_id |
| `EVALUATION_COMPLETED` | EvaluationService | score, quality, feedback     |
| `EXPERIENCE_RECORDED`  | ContinuousLearner | action, reward               |
| `PLUGIN_LOADED`        | PluginRegistry    | name, action                 |
| `AGENT_FAILED`         | Agent runtime     | error, source, context       |
| `TASK_FAILED`          | Task runtime      | error, source, context       |
| `EVALUATION_FAILED`    | EvaluationService | error, source, context       |
| `plugin.activated`     | HotReloadController | plugin, state, op, ok      |
| `plugin.deactivated`   | HotReloadController | plugin, state, op, ok      |
| `plugin.reloaded`      | HotReloadController | plugin, state, op, ok      |
| `plugin.failed`        | HotReloadController | plugin, state, op, ok      |

The four `plugin.*` lifecycle topics are emitted by `core.plugins.hotreload.HotReloadController`
whenever `enable_plugin`/`disable_plugin`/`reload_plugin` completes (see
[Plugins → Lifecycle events](plugins.md#lifecycle-events)) — best-effort,
fire-and-forget, never blocking or failing the lifecycle operation itself.

---

## Event Listener

Collects aggregated metrics automatically:

```python
from core.events import get_event_listener

listener = get_event_listener()

# Aggregated metrics
metrics = listener.get_metrics()
print(metrics["flows"]["total"])
print(metrics["flows"]["success_rate"])
print(metrics["flows"]["avg_duration_ms"])
```

---

## Custom Events

```python
from core.events import get_event_bus

bus = get_event_bus()

# Define custom event
MY_EVENT = "my_plugin.task_completed"

# Subscribe
@bus.on(MY_EVENT)
async def handle_my_event(data):
    await process_completion(data)

# Emit from plugin
await bus.emit(MY_EVENT, {"task_id": "123", "result": "success"})
```

---

## Context Propagation

The EventBus automatically preserves **Context Variables** (via `contextvars`) across asynchronous boundaries.

When an event is emitted, the current `tenant_id` **and** the emitting `user_id`
from the calling context are captured. Both are restored within the handler's
execution environment (for `async` and `sync` handlers alike).

This ensures that:

- Database queries inside handlers automatically target the correct tenant.
- Plugins declaring `tenancy: personal` resolve the **same per-user tenant** inside the handler that the emitter saw, even though the handler may run detached (see [Per-plugin tenancy](../advanced/multi-tenancy.md#per-plugin-tenancy-personal-vs-shared)).
- Security policies and rate limits are applied correctly within the background execution.
- Observability logs maintain the correct correlation.

!!! note "Multi-Tenancy"
    If an event is emitted outside of a tenant context, it defaults to the `default` tenant context within handlers. If no user is bound, the user context is simply left unset in the handler (per-user resolution then falls back to the tenant).

---

## Performance Considerations

Events introduce a small overhead compared to direct calls. Understanding implications is important.

### Latency

**Event Dispatch**: ~0.1-0.5ms per event with few subscribers

**Direct Call**: ~0.01-0.05ms

!!! warning "Hot Path"
    Avoid events in critical code paths where every millisecond counts (e.g., real-time rendering). Prefer direct calls.

### Memory

Every registered subscriber consumes memory. With many listeners:

```python
# ❌ Don't do this
for i in range(10000):
    @bus.on("my_event")
    async def handler():
        pass  # 10k identical handlers!

# ✅ Use one handler with parametric logic
@bus.on("my_event")
async def single_handler(data):
    for processor in processors:
        await processor.handle(data)
```

### Backpressure

There is no queue to absorb slow subscribers. With the default `wait=True`
the emitter itself waits for the slowest handler (bounded only by
`EVENT_HANDLER_TIMEOUT`):

```python
from core.events import get_event_bus

bus = get_event_bus()

# Slow subscriber
@bus.on("heavy_task")
async def slow_processor(data):
    await asyncio.sleep(5)  # Heavy processing

# Every emit blocks for ~5s — the hot path pays for the handler
for i in range(1000):
    await bus.emit("heavy_task", {"id": i})
```

`wait=False` detaches the handlers into background tasks so the emitter no
longer blocks — but a burst then fans out into an unbounded number of
concurrent tasks. **Solution**: keep handlers short and hand heavy work to the
task queue:

```python
from core.task_queue import enqueue_task

@bus.on("heavy_task")
async def enqueue_heavy_task(data):
    # enqueue_task(func, *args, queue="default", **kwargs) -> job id (sync call)
    enqueue_task(process_heavy_task, data["id"])
```

### Best Practices

!!! tip "Fast Event Handler"
    Handlers should be **fast** (<10ms). For long operations, use the task queue.

!!! tip "Avoid Blocking Side-Effects"
    Do not perform blocking I/O in handlers. Always use `async/await`.

!!! tip "Subscriber Limit"
    If you have >50 subscribers for the same event, consider refactoring architecture.

---

## Configuration

```env title=".env"
# Maximum events retained in history ring-buffer
EVENT_MAX_HISTORY=100

# Enable wildcard subscriptions (e.g. "agent.*")
EVENT_ENABLE_WILDCARDS=true

# Enable schema validation on emitted events
EVENT_ENABLE_VALIDATION=false

# Enable dead-letter queue for failed handlers
EVENT_ENABLE_DLQ=false

# Dead-letter queue capacity (oldest entries are dropped beyond it)
EVENT_DLQ_MAX_SIZE=1000

# Max seconds a single handler may run before it is cancelled (default 30)
EVENT_HANDLER_TIMEOUT=30.0
```

The values above are the defaults from `core/config/events.py`
(`EventsConfig`); `.env.example` ships none of the `EVENT_*` keys, so an
untouched deployment runs with exactly these settings.

**Important Parameters**:

- `EVENT_MAX_HISTORY`: Ring-buffer size for event history. Prevents unbounded memory growth.
- `EVENT_ENABLE_VALIDATION`: Attaches an `EventSchemaRegistry` to the bus; `emit()` then rejects payloads that fail a registered schema with `EventValidationError`.
- `EVENT_ENABLE_DLQ`: When enabled, failed or timed-out handlers are forwarded to the dead-letter queue for inspection; `EVENT_DLQ_MAX_SIZE` caps it.
- `EVENT_HANDLER_TIMEOUT`: Hard deadline per handler. Any handler that does not complete within this window is cancelled and its error is recorded (and sent to the DLQ if enabled). Increase for handlers that perform legitimate long I/O; keep it low for latency-sensitive flows.

!!! warning "Handler timeout"
    The default is **30 seconds**. Handlers that call external services without their own timeout will still be cancelled after this deadline. Design handlers to be fast (<100ms) — delegate heavy work to the task queue.
