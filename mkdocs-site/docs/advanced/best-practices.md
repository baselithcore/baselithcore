# Best Practices & Framework Dogmas

To ensure your agents and plugins are high-quality, performant, and maintainable, follow these "Golden Rules" and implementation patterns.

---

## 1. Sacred Core

The `core/` directory is agnostic. Never put domain-specific logic there.

- ❌ **Bad**: Adding `process_jira_ticket()` to `core.utils`.
- ✅ **Good**: Creating a `jira-plugin` that implements the logic.

---

## 2. Plugin-First Architecture

Everything non-essential to the framework's infrastructure should be a plugin.

- **Modularity**: Keep plugins focused on a single responsibility.
- **Independence**: Plugins should not strictly depend on each other unless necessary.

---

## 3. The 4 Dogmas of Baselith-Core

### I. Async By Default

All I/O operations (Database, HTTP, LLM calls) **MUST** be asynchronous.

```python
# ✅ YES
async with httpx.AsyncClient() as client:
    resp = await client.get(url)

# ❌ NO
resp = requests.get(url) 
```

### II. Explicit Lifecycle

Implement `LifecycleMixin` correctly. Resources should be setup in `_do_startup` and cleaned up in `_do_shutdown`.

```python
class MyAgent(LifecycleMixin, AgentProtocol):
    async def _do_startup(self):
        self.client = await create_client()
        
    async def _do_shutdown(self):
        await self.client.close()
```

### III. Dependency Injection (DI)

Use the global DI container to resolve services like LLM or VectorStores.

```python
from core.di import resolve
from core.interfaces import LLMServiceProtocol

llm = resolve(LLMServiceProtocol)
```

### IV. Agent Protocol

All agents must implement `AgentProtocol` to be pluggable into the orchestrator.

```python
async def execute(self, input: str, context: Optional[dict] = None) -> str:
    # Logic here
    return result
```

---

## 4. Gold Standard Implementation

Reference the [Gold Standard Example](/baselith-core/examples/baselith_standard_example.py) for the most complete implementation of these patterns.

### Key Features of a Gold Standard Agent

1. **Inherits** from `LifecycleMixin` and `AgentProtocol`.
2. **Accepts** `agent_id` and `config` in `__init__`.
3. **Validates** state before execution.
4. **Uses** structured logging.
5. **Observes** tenant contexts if applicable.

---

## 5. Security & Resilience

- **Fail Gracefully**: Use try/except blocks around external service calls.
- **No Secrets**: Use environment variables and the `config` system.
