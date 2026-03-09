---
title: First Plugin
description: Step-by-step tutorial to create your first plugin
---



In this tutorial, we'll build a complete plugin that adds a new agent to the system.

---

## Objective

We'll create a plugin called `weather-agent` that:

- Responds to weather-related queries
- Exposes dedicated API endpoints
- Integrates with the orchestrator

---

## Learning Goals

By completing this tutorial, you will:

- Understand the plugin scaffolding process
- Implement intent patterns and flow handlers
- Create a specialized agent with external API integration
- Expose custom REST endpoints
- Write tests for your plugin

---

## 1. Plugin Scaffolding

Use the CLI to generate the base structure:

```bash
baselith plugin create weather-agent --type agent
```

This creates the following structure:

```text
plugins/weather-agent/
├── __init__.py         # Export plugin
├── plugin.py           # Main plugin class
├── agent.py            # Agent logic
└── README.md           # Documentation
```

---

### 2a. Metadata Manifest

Modern plugins use a `manifest.json` file to declare their identity. This allows the framework to discover and analyze plugins without loading their Python code.

```json title="plugins/weather-agent/manifest.json"
{
    "name": "weather-agent",
    "version": "1.0.0",
    "description": "Specialized agent for weather information",
    "author": "Your Name",
    "tags": ["weather", "agent"]
}
```

### 2b. Plugin implementation

Modify `plugin.py` to define capabilities. The metadata is now automatically loaded!

```python title="plugins/weather-agent/plugin.py"
from typing import Any, Dict, List
from core.plugins import AgentPlugin
from .agent import WeatherAgent

class WeatherAgentPlugin(AgentPlugin):
    """Plugin for weather queries with dedicated agent."""

    async def initialize(self, config: Dict[str, Any]) -> None:
        """Initialize the plugin with configuration."""
        self.config = config

    def create_agent(self, **kwargs) -> WeatherAgent:
        return WeatherAgent(
            agent_id="weather-agent",
            config=self.config
        )

    def get_agents(self) -> List[Any]:
        return [self.create_agent()]

    def get_intent_patterns(self) -> List[Dict[str, Any]]:
        """Define patterns that activate this plugin."""
        return [
            {
                "name": "weather",
                "patterns": [
                    "weather", "temperature", "forecast",
                    "rain", "sunny", "climate", "hot", "cold"
                ],
                "priority": 100
            }
        ]

    def get_routers(self) -> List[Any]:
        from .router import router
        return [router]
```

---

## 3. Implement the Agent

Create the agent logic in `agent.py`:

```python title="plugins/weather-agent/agent.py"
from typing import Optional, Dict, Any
import logging
from core.lifecycle import LifecycleMixin, AgentState
from core.orchestration.protocols import AgentProtocol
from core.di import resolve
from core.interfaces import LLMServiceProtocol

logger = logging.getLogger(__name__)

class WeatherAgent(LifecycleMixin, AgentProtocol):
    """Agent for weather queries."""
    
    def __init__(self, agent_id: str, config: Optional[Dict[str, Any]] = None):
        super().__init__()
        self.agent_id = agent_id
        self.config = config or {}
        self.api_key = self.config.get("api_key", "")
        self.llm = resolve(LLMServiceProtocol)
    
    async def _do_startup(self) -> None:
        logger.info(f"Weather Agent {self.agent_id} starting up...")

    async def execute(self, input: str, context: Optional[Dict[str, Any]] = None) -> str:
        if self.state != AgentState.READY:
            return "Agent not ready."
            
        # Implementation of weather logic...
        return f"The weather in the requested city is sunny (simulated)."
```

---

---

## 4. Add API Endpoints

Create a simple router in `router.py`:

```python title="plugins/weather-agent/router.py"
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/weather", tags=["Weather"])

@router.get("/status")
async def get_status():
    return {"status": "ok", "service": "weather"}
```

---

The endpoint will be available at `/api/weather-agent/weather/status`.

---

## 6. Configure the Plugin

Add configuration in `configs/plugins.yaml`:

```yaml title="configs/plugins.yaml"
plugins:
  weather-agent:
    enabled: true
    config:
      api_key: "${WEATHER_API_KEY}"  # From .env
      default_city: "Rome"
```

And in `.env`:

```env
WEATHER_API_KEY=your-api-key-here
```

---

## 7. Test the Plugin

### Verify Loading

```bash
baselith plugin list
```

Expected output:

```text
Loaded Plugins:
  ✅ weather-agent (v1.0.0) - Specialized agent for weather information
```

### Test via Chat

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What is the weather in Milan?"}'
```

### Test Direct API

```bash
curl http://localhost:8000/api/weather-agent/current/Milan
```

---

## 8. Add Tests

Create tests in `tests/plugins/test_weather_agent.py`:

```python title="tests/plugins/test_weather_agent.py"
import pytest
from plugins.weather_agent.agent import WeatherAgent

@pytest.fixture
def agent():
    return WeatherAgent(agent_id="test-key")


class TestWeatherAgent:
    async def test_execute(self, agent):
        response = await agent.execute("What's the weather?")
        assert "sunny" in response.lower()
```

Run the tests:

```bash
pytest tests/plugins/test_weather_agent.py -v
```

---

## Common Pitfalls

!!! warning "API Key Security"
    Never hardcode API keys in code. Always use environment variables and the configuration system.

!!! warning "Blocking I/O"
    Always use `httpx` (async) instead of `requests` (blocking) for HTTP calls.

!!! warning "Error Handling"
    Always handle external API failures gracefully. Return meaningful error messages to users.

---

## Summary

You created a complete plugin that:

- [x] Defines metadata and capabilities
- [x] Implements a specialized agent
- [x] Registers patterns for the intent classifier
- [x] Provides both sync and stream handlers
- [x] Exposes dedicated API endpoints
- [x] Is configurable via YAML
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
