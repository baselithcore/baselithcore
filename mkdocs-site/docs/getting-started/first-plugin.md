---
title: First Plugin
description: Step-by-step tutorial to create your first plugin
---

In this tutorial, we'll build a complete plugin that adds a new agent to the system.

---

## Objective

We'll create a plugin called `weather_agent` that:

- Answers weather-related queries through the configured LLM
- Routes those queries via an intent pattern and a flow handler
- Exposes a dedicated API endpoint

---

## Learning Goals

By completing this tutorial, you will:

- Understand the plugin scaffolding process
- Implement an agent that respects the framework lifecycle
- Inject the LLM service into the agent instead of resolving it globally
- Register an intent pattern and a flow handler with the orchestrator
- Expose a custom REST endpoint
- Write tests for your plugin

---

## 1. Plugin Scaffolding

Use the CLI to generate the base structure:

```bash
baselith plugin create weather_agent --type agent
```

!!! tip "Use an underscore in the directory name"
    The loader imports a plugin as `plugins.<directory-name>` verbatim, so `weather_agent` can be imported from tests with a plain `from plugins.weather_agent.agent import ...`. A hyphenated directory (`weather-agent`) still loads at runtime, but cannot be imported with an `import` statement. Keep the manifest `name` identical to the directory name.

This creates the following structure:

```text
plugins/weather_agent/
├── manifest.json       # Metadata manifest
├── __init__.py         # Exports the plugin class
├── plugin.py           # Plugin class (capabilities)
└── agent.py            # Agent logic
```

The generator derives class names by splitting the plugin name on `-` and `_`, so `weather_agent` yields `WeatherAgentPlugin` and `WeatherAgentAgent`. The listings below shorten the agent class to `WeatherAgent` — replace the generated files with them as you go.

---

## 2. Metadata Manifest

The CLI scaffold writes a `manifest.json`. The framework also accepts `manifest.yaml` / `manifest.yml` (checked first); YAML is convenient when you maintain the manifest by hand or package the plugin for distribution.

```json title="plugins/weather_agent/manifest.json"
{
    "name": "weather_agent",
    "version": "0.3.0",
    "description": "Answers weather questions with the configured LLM",
    "author": "Baselith User",
    "tags": ["agent", "weather"],
    "category": "AI",
    "icon": "bot",
    "readiness": "alpha",
    "environment_variables": []
}
```

The scaffold fills in `version`, `category`, `icon`, `readiness` and an empty `environment_variables` list; edit `description` and `tags` to taste. `Plugin.metadata` reads this file from the plugin directory automatically, so nothing in code repeats it.

!!! tip "YAML vs JSON"
    If you later convert the manifest to `manifest.yaml`, the loader and registry will continue to work without code changes.

---

## 3. Implement the Agent

Replace `agent.py` with the agent logic. The agent receives its LLM service through the constructor — it never looks it up globally — and follows the framework lifecycle via `LifecycleMixin`:

```python title="plugins/weather_agent/agent.py"
"""Weather agent implementation."""

from typing import Any

from core.interfaces import LLMServiceProtocol
from core.lifecycle import AgentState, LifecycleMixin
from core.observability.logging import get_logger
from core.orchestration.protocols import AgentProtocol

logger = get_logger(__name__)


class WeatherAgent(LifecycleMixin, AgentProtocol):
    """Answers weather questions through the injected LLM service."""

    name = "weather-agent"

    def __init__(
        self,
        agent_id: str,
        service: LLMServiceProtocol,
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__()
        self.agent_id = agent_id
        self.service = service
        self.config = config or {}
        self.default_city = self.config.get("default_city", "Rome")

    async def _do_startup(self) -> None:
        logger.info(f"Weather agent {self.agent_id} starting up...")

    async def _do_shutdown(self) -> None:
        logger.info(f"Weather agent {self.agent_id} shutting down...")

    async def execute(self, input: str, context: dict[str, Any] | None = None) -> str:
        if self.state != AgentState.READY:
            return f"Agent not ready (State: {self.state})"

        prompt = (
            "You are a concise weather assistant. If the question names no "
            f"city, assume {self.default_city}.\n\nQuestion: {input}"
        )
        return await self.service.generate_response(prompt)


__all__ = ["WeatherAgent"]
```

Key points:

- `LifecycleMixin` starts every agent in `AgentState.UNINITIALIZED`. Until `await agent.startup()` has run — it calls your `_do_startup()` hook and moves the state to `READY` — `execute()` returns the "not ready" message.
- `service` is anything that satisfies `LLMServiceProtocol`: the real `LLMService` at runtime, a fake in tests.
- `name` is the key the plugin registry stores the agent under.

---

## 4. Plugin Class and Flow Handler

Replace `plugin.py`. The plugin creates the agent in `initialize()` with the shared LLM service, starts it, and registers an intent pattern plus a flow handler for the `weather` intent:

```python title="plugins/weather_agent/plugin.py"
"""WeatherAgent plugin implementation."""

from typing import Any

from core.plugins.agent_plugin import AgentPlugin
from core.services.llm import get_llm_service

from .agent import WeatherAgent


class WeatherFlowHandler:
    """Routes the ``weather`` intent to the agent."""

    def __init__(self, agent: WeatherAgent) -> None:
        self._agent = agent

    async def handle(self, query: str, context: dict[str, Any]) -> dict[str, Any]:
        answer = await self._agent.execute(query, context)
        return {"response": answer, "intent": "weather"}


class WeatherAgentPlugin(AgentPlugin):
    """Plugin providing the WeatherAgent."""

    def __init__(self) -> None:
        super().__init__()
        self._agent: WeatherAgent | None = None

    async def initialize(self, config: dict[str, Any]) -> None:
        """Create the agent with the shared LLM service and start it."""
        await super().initialize(config)
        self._agent = self.create_agent(get_llm_service())
        await self._agent.startup()

    async def shutdown(self) -> None:
        if self._agent is not None:
            await self._agent.shutdown()
        await super().shutdown()

    def create_agent(self, service: Any, **kwargs: Any) -> WeatherAgent:
        """Factory method for the agent."""
        return WeatherAgent(
            agent_id="weather-agent",
            service=service,
            config=self._config,
        )

    def get_agents(self) -> list[Any]:
        return [self._agent] if self._agent is not None else []

    def get_intent_patterns(self) -> list[dict[str, Any]]:
        """Keywords that route a query to the ``weather`` intent."""
        return [
            {
                "name": "weather",
                "patterns": ["weather", "temperature", "forecast", "rain", "sunny"],
                "description": "Questions about current or upcoming weather.",
                "priority": 100,
            }
        ]

    def get_flow_handlers(self) -> dict[str, Any]:
        """Map the ``weather`` intent to its handler."""
        if self._agent is None:
            return {}
        return {"weather": WeatherFlowHandler(self._agent)}

    def get_routers(self) -> list[Any]:
        from .router import router

        return [router]
```

How the pieces fit:

- `create_agent(self, service, **kwargs)` is the abstract factory declared by `AgentPlugin`. Nothing in the core calls it for you: the plugin calls it itself, here with `get_llm_service()` — the process-wide LLM service configured from `.env`.
- `get_intent_patterns()` returns dictionaries keyed by `name`. The registry stores each under its intent name; the orchestrator's intent classifier matches `patterns` as lower-cased substrings of the query and passes `description` to LLM-based classification.
- `get_flow_handlers()` maps an intent name to a handler exposing `async handle(query, context) -> dict`. When the classifier picks `weather`, the orchestrator invokes that handler and reads the `response` key of the returned dictionary.

Update `__init__.py` to export the renamed class:

```python title="plugins/weather_agent/__init__.py"
"""weather_agent plugin."""

from .plugin import WeatherAgentPlugin

__all__ = ["WeatherAgentPlugin"]
```

---

## 5. Add API Endpoints

The agent scaffold does not create a router; add `router.py` yourself:

```python title="plugins/weather_agent/router.py"
"""Weather plugin API endpoints."""

from fastapi import APIRouter

router = APIRouter(prefix="/weather", tags=["Weather"])


@router.get("/status")
async def get_status() -> dict[str, str]:
    return {"status": "ok", "service": "weather"}
```

Plugin routers are mounted under `get_router_prefix()`, which defaults to `/api/<manifest name>`, plus the router's own prefix — so this route lives at `/api/weather_agent/weather/status`.

---

## 6. Configure the Plugin

`configs/plugins.yaml` is a flat mapping: each top-level key is a plugin name (the directory name, the manifest name, or either with `-`/`_` swapped) and its value is handed as-is to `initialize(config)`:

```yaml title="configs/plugins.yaml"
weather_agent:
  enabled: true
  default_city: Milan
```

There is no `plugins:` wrapper, no `config:` sub-key and no `${VAR}` interpolation — values are literal. Read them with `self.get_config("default_city")` or, as above, by passing the whole mapping to the agent.

Secrets never go in `plugins.yaml`. Put them in a plugin-local `plugins/weather_agent/.env`. When the plugin loads, keys in the plugin's own `WEATHER_AGENT_` namespace (or listed verbatim in the manifest's `environment_variables`) are exported to `os.environ` without overriding variables that are already set, and every key is merged into the plugin config under its lower-cased name:

```env title="plugins/weather_agent/.env"
WEATHER_AGENT_API_KEY=your-api-key-here
```

`self.get_config("weather_agent_api_key")` then returns it. Framework-global settings are refused from a plugin `.env`.

---

## 7. Test the Plugin

### Verify Loading

```bash
baselith plugin list
```

The command prints a Rich table (`Local Plugin Status`) with one row per directory under `plugins/`. The `Config` column shows `✓` when the plugin appears in `configs/plugins.yaml` with `enabled: true`:

```text
                              Local Plugin Status
┏━━━━━━━━━━━━┳━━━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━┓
┃   Status   ┃ Plugin Name   ┃ Version ┃ Type  ┃ Readiness ┃ Config ┃ Components ┃
┡━━━━━━━━━━━━╇━━━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━┩
│ ✅ Active  │ weather_agent │ 0.3.0   │ Agent │ alpha     │   ✓    │ Agent      │
└────────────┴───────────────┴─────────┴───────┴───────────┴────────┴────────────┘
```

### Test via Chat

Start the server with `baselith run` and send a query containing one of the patterns. The classifier resolves the `weather` intent and the orchestrator calls the flow handler, which delegates to the agent:

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $BASELITH_TOKEN" \
  -d '{"query": "What is the weather in Milan?"}'
```

!!! note "Chat routes require auth"
    `POST /chat` and `POST /chat/stream` are protected by the `require_user`
    dependency, and the request model uses `query` (not `message`).

### Test Direct API

This hits the router defined above. Plugin routers are mounted at
`/api/{plugin-name}` plus the router's own prefix, so the `/status` route lives at:

```bash
curl http://localhost:8000/api/weather_agent/weather/status
```

---

## 8. Add Tests

Keep unit tests next to the plugin, as `plugins/example-plugin/tests/` does, and inject a fake LLM service: `ServiceRegistry` is only populated while the application lifespan runs, so a test must never resolve services globally. Remember to start the agent — before `startup()` it is `UNINITIALIZED` and `execute()` refuses to run:

```python title="plugins/weather_agent/tests/test_agent.py"
"""Unit tests for the weather agent."""

import pytest

from plugins.weather_agent.agent import WeatherAgent
from plugins.weather_agent.plugin import WeatherAgentPlugin


class FakeLLM:
    """Stands in for the LLM service - no network, deterministic output."""

    async def generate_response(self, prompt, model=None, json=False):
        return "Sunny, 24 C"


@pytest.fixture
async def agent():
    agent = WeatherAgent(
        agent_id="weather-test", service=FakeLLM(), config={"default_city": "Milan"}
    )
    await agent.startup()
    yield agent
    await agent.shutdown()


async def test_execute_uses_llm(agent):
    response = await agent.execute("What's the weather like?")
    assert "sunny" in response.lower()


async def test_not_ready_before_startup():
    agent = WeatherAgent(agent_id="weather-test", service=FakeLLM())
    response = await agent.execute("Any rain today?")
    assert response.startswith("Agent not ready")


def test_plugin_metadata_and_routes():
    plugin = WeatherAgentPlugin()
    assert plugin.metadata.name == "weather_agent"
    assert plugin.get_router_prefix() == "/api/weather_agent"
    assert plugin.get_intent_patterns()[0]["name"] == "weather"
```

Run the tests from the repository root:

```bash
python -m pytest plugins/weather_agent/tests -v --no-cov
```

`asyncio_mode = auto` in `pytest.ini` runs the async fixture and tests without extra markers; `--no-cov` skips the repository-wide coverage gate, which is meant for the full `tests/` suite.

---

## Common Pitfalls

!!! warning "API Key Security"
    Never hardcode API keys in code or in `configs/plugins.yaml`. Use the plugin-local `.env` (namespaced keys) or the process environment.

!!! warning "Blocking I/O"
    Always use `httpx` (async) instead of `requests` (blocking) for HTTP calls.

!!! warning "Error Handling"
    Always handle external API failures gracefully. Return meaningful error messages to users.

---

## Summary

You created a complete plugin that:

- [x] Defines metadata in a manifest
- [x] Implements a lifecycle-aware agent with an injected LLM service
- [x] Registers an intent pattern for the classifier
- [x] Provides a flow handler for the `weather` intent
- [x] Exposes a dedicated API endpoint
- [x] Is configurable via `configs/plugins.yaml`
- [x] Includes tests

---

## Next Steps

<div class="feature-grid" markdown>

<div class="feature-card" markdown>

### :material-puzzle: Plugin Architecture

Deep dive into the [plugin system architecture](../plugins/architecture.md).

</div>

<div class="feature-card" markdown>

### :material-transit-connection-variant: Flow Handlers

Learn how to handle [complex flows](../plugins/flow-handlers.md).

</div>

<div class="feature-card" markdown>

### :material-test-tube: Testing

Explore comprehensive [testing strategies](../advanced/testing.md).

</div>

</div>
