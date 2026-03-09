---
title: Creating Plugins
description: Step-by-step tutorial for creating plugins
---

<!-- markdownlint-disable-file MD046 MD025 -->

A practical guide to plugin development.

---

## 1. Scaffold with CLI

Use the CLI to generate the plugin structure:

```bash
baselith plugin create my-plugin --type agent
baselith plugin create --interactive  # Launch the wizard
```

Expected output:

```text
✅ Created plugins/my-plugin/
  ├── manifest.json
  ├── __init__.py
  ├── plugin.py
  ├── agent.py
  └── README.md
```

This command creates a complete plugin skeleton with all necessary files based on the selected type.

!!! tip "Plugin Types"
    - `agent`: Creates a plugin with flow handlers and agent logic
    - `router`: Creates a plugin focused on API endpoints
    - `generic`: Creates a minimal plugin structure for custom use cases

---

## 2. Define Metadata

Every plugin must declare metadata. Since the introduction of the Hybrid Manifest system, new plugins use a `manifest.json` file for static metadata, which is then loaded in `plugin.py`.

### 2a. The Manifest File

The `manifest.json` file contains the plugin's identity and description:

```json title="plugins/my-plugin/manifest.json"
{
    "name": "my-plugin",
    "version": "1.0.0",
    "description": "My custom plugin",
    "author": "Your Name",
    "tags": ["agent", "my-plugin"],
    "category": "AI",
    "icon": "bot",
    "readiness": "alpha",
    "environment_variables": ["API_KEY"]
}
```

```python title="plugins/my-plugin/plugin.py"
from pathlib import Path
from core.plugins import AgentPlugin
from .agent import MyAgent

class MyPlugin(AgentPlugin):
    async def initialize(self, config: dict) -> None:
        self.config = config
    
    # ... rest of implementation ...
```

### Metadata Fields

| Field                   | Required | Description                                                  |
| ----------------------- | -------- | ------------------------------------------------------------ |
| `name`                  | Yes      | Unique plugin identifier (lowercase, hyphens only)           |
| `version`               | Yes      | Semantic version (e.g., `1.2.3`)                             |
| `description`           | Yes      | Brief description of plugin functionality                    |
| `author`                | No       | Plugin author name or organization                           |
| `tags`                  | No       | Keywords for categorization and search                       |
| `category`              | No       | Primary category (e.g., `AI`, `Security`, `Utilities`)       |
| `icon`                  | No       | Relative path or icon name (e.g., `bot`, `link`)             |
| `readiness`             | No       | Development stage (`alpha`, `beta`, `stable`)                |
| `environment_variables` | No       | List of required environment variables for `baselith doctor` |
| `python_dependencies`   | No       | List of required Python packages (`pip install` format)      |

!!! warning "Naming Convention"
    Plugin names must be lowercase with hyphens only. Use descriptive names like `weather-agent`, not `WeatherAgent` or `weather_agent`.

---

## 3. Implement the Agent

The agent logic should reside in `agent.py` and implement the `AgentProtocol`:

```python title="plugins/my-plugin/agent.py"
from typing import Optional, Dict, Any
from core.lifecycle import LifecycleMixin, AgentState
from core.orchestration.protocols import AgentProtocol
import logging

logger = logging.getLogger(__name__)

class MyAgent(LifecycleMixin, AgentProtocol):
    def __init__(self, agent_id: str, config: Optional[Dict[str, Any]] = None):
        super().__init__()
        self.agent_id = agent_id
        self.config = config or {}

    async def _do_startup(self) -> None:
        logger.info(f"Agent {self.agent_id} starting up...")

    async def execute(self, input: str, context: Optional[Dict[str, Any]] = None) -> str:
        if self.state != AgentState.READY:
            return "Agent not ready."
            
        return f"Processed: {input}"
```

---

---

## 4. Register Agents and Intents

The orchestrator discovers agents and routes requests based on intent patterns:

```python title="plugins/my-plugin/plugin.py"
class MyPlugin(AgentPlugin):
    # ... metadata and initialize ...
    
    def get_agents(self) -> list:
        """Register the agents provided by this plugin."""
        return [self.create_agent()]
    
    def get_intent_patterns(self) -> list:
        """
        Define patterns that trigger this plugin's intents.
        """
        return [
            {
                "name": "my_intent",
                "patterns": ["keyword1", "keyword2", "analyze"],
                "priority": 100
            }
        ]
```

### Intent Pattern Structure

| Field      | Type        | Description                                         |
| ---------- | ----------- | --------------------------------------------------- |
| `name`     | `str`       | Unique intent identifier                            |
| `patterns` | `list[str]` | Keywords or phrases that trigger this intent        |
| `priority` | `int`       | Routing priority (higher = preferred). Default: 100 |

!!! tip "Routing"
    The orchestrator uses these patterns to identify when a user request should be handled by your plugin's agents.

---

## 5. Add API Endpoints (Optional)

Expose custom API endpoints by returning FastAPI routers:

```python title="plugins/my-plugin/router.py"
from fastapi import APIRouter
from pydantic import BaseModel

class QueryRequest(BaseModel):
    query: str

router = APIRouter(prefix="/my-plugin", tags=["My Plugin"])

@router.get("/status")
async def status():
    return {"status": "ok"}
```

In your `plugin.py`:

```python
    def get_routers(self) -> list:
        from .router import router
        return [router]
```

### Automatic API Registration

When a plugin implements `create_router()`, endpoints are automatically:

- Registered at `/api/{plugin-name}/*`
- Included in OpenAPI documentation
- Protected by framework authentication (if enabled)
- Tagged for easy discovery in Swagger UI

**Accessing endpoints:**

```bash
# Health check
curl http://localhost:8000/api/my-plugin/status

# Direct processing
curl -X POST http://localhost:8000/api/my-plugin/process \
  -H "Content-Type: application/json" \
  -d '{"query": "test"}' ```

---

## 6. Configure Plugin

Define plugin-specific configuration in `configs/plugins.yaml`:

```yaml title="configs/plugins.yaml"
plugins:
  my-plugin:
    enabled: true
    config:
      custom_setting: "value"
      api_timeout: 30
      max_retries: 3
```

### Configuration Access

Access configuration in the agent:

```python
class MyAgent(LifecycleMixin, AgentProtocol):
    def __init__(self, agent_id: str, config: Optional[Dict[str, Any]] = None):
        super().__init__()
        self.timeout = config.get("api_timeout", 10)
```

!!! tip "Environment Variables"
    Override configuration via environment variables:
    ```bash
    export PLUGIN_MY_PLUGIN_API_TIMEOUT=60
    ```

---

## 7. Test the Plugin

### Verify Plugin Loading

```bash
# List all loaded plugins
baselith plugin list

# Check specific plugin status
baselith plugin status my-plugin

# Verify dependencies
baselith plugin deps check my-plugin

# Visualize dependency tree
baselith plugin tree my-plugin
```

Expected output:

```text
✅ my-plugin (0.1.0)
  Status: Active
  Agents: MyAgent
  Endpoints: /api/my-plugin/status
```

---

## 8. Custom CLI Commands

Plugins can extend the `baselith` CLI by providing a `cli.py` file in their root directory. The framework automatically scans and registers these commands at startup.

### Implementation

Create a `cli.py` file that implements the `register_parser` function:

```python title="plugins/my-plugin/cli.py"
import argparse

def cmd_my_feature(args):
    print(f"Executing my-feature for {args.name}")
    return 0

def register_parser(subparsers, formatter_class):
    """
    Register custom commands into the main Baselith CLI.
    """
    my_parser = subparsers.add_parser(
        "my-feature",
        help="Custom feature provided by MyPlugin",
        formatter_class=formatter_class
    )
    my_parser.add_argument("name", help="Name to process")
    my_parser.set_defaults(func=cmd_my_feature)
    return my_parser
```

### Usage

Once the plugin is enabled, your command becomes available globally:

```bash
baselith my-feature "Test Name"
```

!!! tip "Professional Output"
    Use the `core.cli.ui` components (like `console`, `print_success`, `Table`) within your custom commands to maintain the premium look and feel of the framework.

### Test via API

```bash
# Test plugin endpoint
curl http://localhost:8000/api/my-plugin/status

# Test via chat orchestration
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "keyword1 something"}'
```

### Test via Web UI

1. Navigate to `http://localhost:8000`
2. Send a message containing one of your patterns (e.g., "keyword1 test")
3. Verify the plugin handler is invoked

!!! tip "Debug Mode"
    Enable debug logging to see intent classification:
    ```bash
    export LOG_LEVEL=DEBUG
    baselith run
    ```

---

## 9. Add Unit Tests

Create comprehensive tests for your agents:

```python title="tests/plugins/test_my_plugin.py"
import pytest
from plugins.my_plugin.agent import MyAgent

class TestMyPlugin:
    @pytest.fixture
    def agent(self):
        """Create agent instance."""
        return MyAgent(agent_id="test-agent")
    
    async def test_execute(self, agent):
        """Test agent execution."""
        await agent.initialize() # If LifecycleMixin is used
        result = await agent.execute("test query", {})
        assert result is not None
        assert isinstance(result, str)
```

### Run Tests

```bash
# Run all plugin tests
pytest tests/plugins/test_my_plugin.py -v

# Run with coverage
pytest tests/plugins/test_my_plugin.py --cov=plugins.my_plugin
```

---

## 10. Maintenance and Lifecycle

Manage your local plugins using several CLI utilities:

### Validation

Before deploying or testing, validate that your plugin conforms to the framework interfaces:

```bash
baselith plugin validate my-plugin
```

### Disabling/Enabling

Temporarily deactivate a plugin without deleting its files:

```bash
baselith plugin disable my-plugin
baselith plugin enable my-plugin
```

### Deletion

Permanently remove a plugin from the local development environment:

```bash
baselith plugin delete my-plugin
```

### Migrating Legacy Plugins

If you have plugins created before the introduction of the Hybrid Manifest system (where metadata was defined via a python `@property`), you must migrate them.

**Option A: CLI Export (Recommended)**

Use the built-in manifest exporter to generate a `manifest.json` from your existing Python metadata:

```bash
baselith plugin export-manifest my-legacy-plugin
```

**Option B: Migration Script**

Alternatively, use the migration utility script:

```bash
python scripts/migrate_plugins.py plugins/my-legacy-plugin
```

This will automatically extract the metadata from your Python file, generate a `manifest.json`, and remove the obsolete `metadata` method from your code.

---

## Next Steps

After creating your plugin:

- **Document**: Add comprehensive `README.md` to your plugin directory
- **Distribute**: See [Packaging Guide](packaging.md) to prepare for distribution
- **Extend**: Add [Frontend Integration](frontend-integration.md) for custom UI
- **Publish**: Submit to [Plugin Marketplace](marketplace.md)

---

## Troubleshooting

??? failure "Plugin not loading"
    **Symptom**: Plugin doesn't appear in `plugin list`

    **Diagnosis**:
    ```bash
    baselith plugin status my-plugin
    ```
    
    **Common causes**:
    - Plugin disabled in `configs/plugins.yaml`
    - Syntax error in `plugin.py`
    - Missing required dependencies
    
    **Solution**: Check logs and fix errors shown

??? failure "Intent not triggering"
    **Symptom**: Messages with patterns don't invoke plugin handler

    **Diagnosis**: Check orchestration logs with `DEBUG` logging
    
    **Common causes**:
    - Lower priority than competing intents
    - Patterns too generic (e.g., just "help")
    - Handler registration issue
    
    **Solution**: Increase priority or make patterns more specific

??? failure "API endpoints returning 404"
    **Symptom**: `/api/my-plugin/*` returns 404

    **Common causes**:
    - `create_router()` not implemented in `router.py`
    - Router not returning `APIRouter` instance
    - Plugin not implementing `RouterPlugin` mixin
    
    **Solution**: Verify plugin inherits `RouterPlugin` and returns router
