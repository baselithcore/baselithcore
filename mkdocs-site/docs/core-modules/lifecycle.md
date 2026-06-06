# Lifecycle Management

The `core/lifecycle/` module provides deterministic startup/shutdown hooks and a mixin pattern for components that need managed lifecycle phases.

## Module Structure

```yaml
core/lifecycle/
├── protocols.py      # AgentLifecycle, AgentHooks, AgentState, HealthStatus
├── mixins.py         # LifecycleMixin — startup()/shutdown() template methods
├── deterministic.py  # apply_deterministic_mode() + get_llm_override_kwargs()
└── errors.py         # BaseFrameworkError + LifecycleError/AgentError/... and FrameworkErrorCode
```

---

## LifecycleMixin

Base class for any component that requires explicit initialization and
teardown. It implements the public `startup()` / `shutdown()` template
methods, which manage an `AgentState` machine
(`UNINITIALIZED → STARTING → READY → STOPPING → STOPPED`) and call the
override hooks `_do_startup()` / `_do_shutdown()`:

```python
from core.lifecycle.mixins import LifecycleMixin

class MyService(LifecycleMixin):

    async def _do_startup(self) -> None:
        """Override hook — called by startup() once on initialization."""
        self._connection = await connect_to_db()

    async def _do_shutdown(self) -> None:
        """Override hook — called by shutdown() once on teardown."""
        await self._connection.close()

# Usage
svc = MyService()
await svc.startup()    # transitions to READY, runs _do_startup
# ... use service ...
await svc.shutdown()   # transitions to STOPPED, runs _do_shutdown
```

Additional override hooks are available: `_do_reset()` and
`_do_health_check()`. The mixin also exposes `state`, `hooks`,
`health_check()`, `reset()`, `pause()`, and `resume()`. There is no async
context-manager support — call `startup()`/`shutdown()` explicitly.

!!! note "No start()/stop() aliases"
    The public methods are `startup()` and `shutdown()`. There are no
    `start()`/`stop()` methods, and the override hooks are `_do_startup` /
    `_do_shutdown` (not `_on_start` / `_on_stop`).

---

## Deterministic Mode

`core/lifecycle/deterministic.py` provides reproducibility helpers, gated on
`deterministic_mode` in the core config. They are plain module functions —
there is no startup-ordering manager here.

```python
from core.lifecycle.deterministic import (
    apply_deterministic_mode,
    get_llm_override_kwargs,
)

# Seed random / numpy / PYTHONHASHSEED (no-op unless deterministic_mode is on)
apply_deterministic_mode(seed=42)

# LLM kwargs that pin sampling when deterministic_mode is on, else {}
overrides = get_llm_override_kwargs()
# -> {"temperature": 0.0, "seed": <random_seed>, "top_p": 1.0} when enabled
llm_response = await llm.complete(prompt, **overrides)
```

When `deterministic_mode` is disabled, `apply_deterministic_mode()` returns
without changing anything and `get_llm_override_kwargs()` returns `{}`.

---

## Lifecycle Errors

`core/lifecycle/errors.py` defines the framework error hierarchy. All errors
derive from `BaseFrameworkError`, which carries a `code`
(`FrameworkErrorCode`), an optional `context` dict, a `recoverable` flag, and
a `to_dict()` serializer for logging/API responses.

| Class | Purpose |
|-------|---------|
| `BaseFrameworkError` | Base for all framework errors |
| `LifecycleError` | Lifecycle/startup/shutdown failures (e.g. `_do_startup` raised) |
| `AgentError` | Errors during agent execution |
| `RecoverableError` | Expected, retryable failures (`recoverable=True`) |
| `FatalError` | Critical, non-recoverable failures |

`startup()` wraps any `_do_startup()` exception in a `LifecycleError` with
code `FrameworkErrorCode.LIFECYCLE_START_FAILED` and re-raises it.

```python
from core.lifecycle.errors import LifecycleError, FrameworkErrorCode

try:
    await svc.startup()
except LifecycleError as exc:
    print(exc.code)        # FrameworkErrorCode.LIFECYCLE_START_FAILED
    print(exc.to_dict())   # serializable error payload
```

---

## Lazy Resource Initialization

To optimize startup time and memory footprint, the framework supports **Lazy Resource Initialization**. Instead of starting all core services (PostgreSQL, Redis, Vector Database, LLM providers) at once, the system analyzes the requirements of enabled plugins:

1. **Requirement Analysis**: The `ResourceAnalyzer` performs static analysis of plugin code to identify dependencies on specific core services (e.g., `postgres`, `vectorstore`, `llm`).
2. **On-Demand Factory Registration**: Core services are registered as "Lazy Factories" in the `ServiceRegistry`.
3. **Just-In-Time Startup**: A service is only initialized the first time a plugin that requires it is activated.

This mechanism ensures that a "headless" core or a core with minimal plugins remains extremely lightweight.

---

## FastAPI Integration

The lifecycle is integrated with FastAPI's lifespan system. Components that
subclass `LifecycleMixin` are driven via their `startup()` / `shutdown()`
methods inside the lifespan context:

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI

@asynccontextmanager
async def lifespan(app: FastAPI):
    await my_service.startup()   # transitions component to READY
    yield
    await my_service.shutdown()  # transitions component to STOPPED

app = FastAPI(lifespan=lifespan)
```

!!! tip "Plugin Lifecycle"
    Plugins can hook into the lifecycle by registering a `LifecycleMixin`
    subclass in their `plugin.py`. The framework drives it via `startup()`
    before serving and `shutdown()` on teardown.
