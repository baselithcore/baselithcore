# Lifecycle Management

The `core/lifecycle/` module provides deterministic startup/shutdown hooks and a mixin pattern for components that need managed lifecycle phases.

## Module Structure

```yaml
core/lifecycle/
├── protocols.py      # Lifecycle protocol interface
├── mixins.py         # LifecycleMixin — base class with start/stop
├── deterministic.py  # Ordered startup sequencing
└── errors.py         # LifecycleError, StartupError, ShutdownError
```

---

## LifecycleMixin

Base class for any component that requires explicit initialization and teardown:

```python
from core.lifecycle.mixins import LifecycleMixin

class MyService(LifecycleMixin):

    async def _on_start(self) -> None:
        """Called once on startup."""
        self._connection = await connect_to_db()

    async def _on_stop(self) -> None:
        """Called once on shutdown."""
        await self._connection.close()

# Usage
svc = MyService()
await svc.start()   # triggers _on_start
# ... use service ...
await svc.stop()    # triggers _on_stop

# Or use as async context manager
async with MyService() as svc:
    await svc.do_work()
```

---

## Deterministic Startup Ordering

The `DeterministicStartup` manager ensures services start and stop in the correct dependency order:

```python
from core.lifecycle.deterministic import DeterministicStartup

startup = DeterministicStartup()

# Register components with dependencies
startup.register(db_service, name="db")
startup.register(cache_service, name="cache", depends_on=["db"])
startup.register(llm_service, name="llm", depends_on=["cache"])

# Starts in order: db → cache → llm
await startup.start_all()

# Stops in reverse: llm → cache → db
await startup.stop_all()
```

---

## FastAPI Integration

The lifecycle is integrated with FastAPI's lifespan system in `core/bootstrap/`:

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI

@asynccontextmanager
async def lifespan(app: FastAPI):
    await startup.start_all()   # All services start
    yield
    await startup.stop_all()    # All services stop cleanly

app = FastAPI(lifespan=lifespan)
```

!!! tip "Plugin Lifecycle"
    Plugins can hook into the lifecycle by registering a `LifecycleMixin` subclass in their `plugin.py`. The framework guarantees `start()` is called before the first request and `stop()` after the last.
