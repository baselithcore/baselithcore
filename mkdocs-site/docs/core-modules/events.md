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
├── bus.py            # Main event bus
├── listener.py       # Event listener
├── names.py          # Event names constants
└── types.py          # Event types
```

---

## Event Bus

Asynchronous Pub/Sub:

```python
from core.events import get_event_bus, EventNames

bus = get_event_bus()

# Subscribe
@bus.on(EventNames.FLOW_COMPLETED)
async def on_flow_complete(data: dict):
    print(f"Flow {data['intent']} completed in {data['duration_ms']}ms")

# Emit
await bus.emit(EventNames.FLOW_COMPLETED, {
    "intent": "weather",
    "duration_ms": 150,
    "success": True
})
```

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

@bus.on(EventNames.ERROR_OCCURRED)
async def audit_error(data: dict):
    await db.insert_audit_log(
        action="error",
        source=data["source"],
        error=data["error"],
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
| `FLOW_COMPLETED`       | Orchestrator      | intent, duration_ms, success |
| `EVALUATION_COMPLETED` | EvaluationService | score, quality, feedback     |
| `EXPERIENCE_RECORDED`  | ContinuousLearner | action, reward               |
| `PLUGIN_LOADED`        | PluginRegistry    | name, action                 |
| `ERROR_OCCURRED`       | Any               | error, source, context       |

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

When an event is emitted, the current `tenant_id` from the calling context is captured. This context is then restored within the handler's execution environment (both for `async` and `sync` handlers).

This ensures that:

- Database queries inside handlers automatically target the correct tenant.
- Security policies and rate limits are applied correctly within the background execution.
- Observability logs maintain the correct correlation.

!!! note "Multi-Tenancy"
    If an event is emitted outside of a tenant context, it defaults to the `default` tenant context within handlers.

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

If subscribers are slow, the event queue can grow:

```python
from core.events import get_event_bus

bus = get_event_bus()

# Slow subscriber
@bus.on("heavy_task")
async def slow_processor(data):
    await asyncio.sleep(5)  # Heavy processing

# If you emit events faster than they are processed,
# memory will grow
for i in range(1000):
    await bus.emit("heavy_task", {"id": i})  # Potential problem!
```

**Solution**: Use the task queue for heavy processing instead of synchronous events:

```python
from core.task_queue import enqueue

@bus.on("heavy_task")
async def enqueue_heavy_task(data):
    # Enqueue instead of executing directly
    await enqueue("process_heavy_task", task_id=data["id"])
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
# Maximum number of listeners per event
EVENTS_MAX_LISTENERS=100

# Enable automatic metrics collection
EVENTS_ENABLE_METRICS=true

# Event logging level
EVENTS_LOG_LEVEL=INFO
```

**Important Parameters**:

- `EVENTS_MAX_LISTENERS`: Prevents memory leaks from accidental infinite registrations
- `EVENTS_ENABLE_METRICS`: Useful for debugging but has overhead (~5-10%)
- `EVENTS_LOG_LEVEL`: `DEBUG` shows every emit/receive (verbose in production)
