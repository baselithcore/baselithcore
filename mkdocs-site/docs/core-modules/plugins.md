---
title: Plugin System
description: Registry, lifecycle, loader, and plugin metrics
---



The `core/plugins` module manages the complete lifecycle of plugins within the system, providing a robust framework for extension and modularity.

---

## Module Structure

```text
core/plugins/
├── __init__.py           # Public exports
├── interface.py          # Base Plugin class
├── manifest.py           # Manifest loading and validation
├── agent_plugin.py       # AgentPlugin mixin
├── router_plugin.py      # RouterPlugin mixin
├── graph_plugin.py       # GraphPlugin mixin
├── registry.py           # PluginRegistry implementation
├── loader.py             # PluginLoader implementation
├── lifecycle.py          # Lifecycle management
├── hotreload.py          # Hot reload support
├── metrics.py            # Plugin metrics collection
├── health.py             # Health checking
├── version.py            # Version management
├── lookup.py             # Plugin lookup utilities
├── registration.py       # Registration logic
└── resource_analyzer.py  # AST-based static analysis
```

---

## Plugin Base Class

Modern plugins use a `manifest.json` (or `.yaml`) for metadata, which is automatically loaded by the framework.

```json title="plugins/my-plugin/manifest.json"
{
    "name": "my-plugin",
    "version": "1.0.0",
    "description": "An example plugin",
    "author": "Your Name"
}
```

```python title="plugins/my-plugin/plugin.py"
from core.plugins import Plugin

class MyPlugin(Plugin):
    """Example Plugin implementation."""
    
    async def initialize(self, config: dict) -> None:
        """Plugin initialization logic."""
        self.config = config
    
    async def shutdown(self) -> None:
        """Cleanup resources on shutdown."""
        pass
```

---

## Capability Mixins

Mixins allow plugins to expose specific capabilities, such as Agents, APIs, or Graph extensions.

### AgentPlugin

Use this mixin for plugins that provide agents.

```python
from core.plugins import AgentPlugin, PluginMetadata

class MyPlugin(AgentPlugin):
    def create_agent(self, **kwargs) -> MyAgent:
        return MyAgent(agent_id="main-agent", config=self.config)

    def get_agents(self) -> list:
        return [self.create_agent()]
    
    def get_intent_patterns(self) -> list:
        return [
            {
                "name": "my_intent",
                "patterns": ["keyword1", "keyword2"],
                "priority": 100
            }
        ]
```

### RouterPlugin

Use this mixin to expose REST API endpoints via FastAPI.

```python
from core.plugins import Plugin, RouterPlugin
from fastapi import APIRouter

class MyPlugin(Plugin, RouterPlugin):
    def create_router(self) -> APIRouter:
        router = APIRouter()
        
        @router.get("/status")
        async def get_status():
            return {"status": "ok"}
        
        return router
    
    def get_router_prefix(self) -> str:
        return "/my-plugin"  # Default: /<plugin-name>
```

### GraphPlugin

Use this mixin to extend the system's Knowledge Graph schema.

```python
from core.plugins import Plugin, GraphPlugin

class MyPlugin(Plugin, GraphPlugin):
    def register_entity_types(self) -> list:
        return ["CustomEntity", "CustomRelation"]
    
    def get_graph_service(self):
        from .graph_service import MyGraphService
        return MyGraphService()
```

---

## PluginRegistry

The `PluginRegistry` serves as the central catalog for all active plugins.

```python
from core.plugins import get_plugin_registry

registry = get_plugin_registry()

# List all loaded plugins
for plugin in registry.get_all():
    print(f"{plugin.name}: {plugin.version}")

# Retrieve a specific plugin instance
weather = registry.get("weather-agent")

# Find a handler for a specific intent
handler = registry.get_handler("weather")

# Check if a plugin is registered
if registry.is_registered("my-plugin"):
    ...
```

### Thread Safety

The registry is designed to be thread-safe for concurrent access.

```python
class PluginRegistry:
    def __init__(self):
        self._lock = threading.RLock()
        self._plugins: dict[str, Plugin] = {}
    
    def register(self, plugin: Plugin) -> None:
        with self._lock:
            self._plugins[plugin.name] = plugin
```

---

## PluginLoader

The `PluginLoader` handles discovering and loading plugins from the filesystem.

```python
from core.plugins import PluginLoader

loader = PluginLoader(plugins_dir="plugins/")

# Load all plugins in the directory
plugins = await loader.load_all()

# Load a specific plugin by name
plugin = await loader.load("weather-agent")
```

### Lazy Loading

The system uses [Lazy Loading](../advanced/lazy-loading.md) to optimize startup time.

```mermaid
sequenceDiagram
    participant Loader
    participant Analyzer as ResourceAnalyzer
    participant Plugin
    
    Loader->>Analyzer: Analyze metadata (AST)
    Note over Analyzer: No Python imports
    Analyzer-->>Loader: Static Metadata
    Loader->>Loader: Register Proxy
    
    Note over Loader,Plugin: On first use...
    
    Loader->>Plugin: Import and Init
    Plugin-->>Loader: Instance Ready
```

### ResourceAnalyzer

Performs static analysis to extract metadata without executing code, which is crucial for performance.

```python
from core.plugins import ResourceAnalyzer

analyzer = ResourceAnalyzer()

# Extract metadata without importing the module
metadata = analyzer.analyze_plugin("plugins/weather-agent/")

print(metadata.name)           # "weather-agent"
print(metadata.version)        # "1.0.0"
print(metadata.dependencies)   # ["httpx", "pydantic"]
```

---

## Lifecycle Management

Plugins go through a defined lifecycle state machine.

```mermaid
stateDiagram-v2
    [*] --> Discovered: Scan directory
    Discovered --> Registered: Analyze metadata
    Registered --> Initializing: First access
    Initializing --> Active: initialize() success
    Active --> Stopping: shutdown signal
    Stopping --> Stopped: shutdown() complete
    Stopped --> [*]
    
    Initializing --> Failed: Error
    Active --> Failed: Runtime error
```

### Lifecycle Hooks

Implement these methods to manage your plugin's state.

```python
class MyPlugin(Plugin):
    async def initialize(self, config: dict) -> None:
        """Called upon first use, before processing requests."""
        self.db = await connect_database()
    
    async def on_ready(self) -> None:
        """Called when the entire system is fully ready."""
        await self.warm_cache()
    
    async def shutdown(self) -> None:
        """Called during system shutdown."""
        await self.db.close()
```

---

## Hot Reload

The `HotReloader` allows reloading plugins code without restarting the entire server—ideal for development.

```python
from core.plugins import HotReloader

reloader = HotReloader()

# Watch directory for changes
await reloader.watch("plugins/")

# Callback on change
@reloader.on_change
async def handle_reload(plugin_name: str):
    print(f"Plugin {plugin_name} reloaded")
```

---

## Health Checks

Ensure robust operation by validating plugin health.

```python
from core.plugins import PluginHealthChecker

checker = PluginHealthChecker()

# Check health of all plugins
health = await checker.check_all()

for plugin_name, status in health.items():
    print(f"{plugin_name}: {status.healthy}")
    if not status.healthy:
        print(f"  Error: {status.error}")
```

---

## Metrics

Monitor plugin performance using `PluginMetrics`.

```python
from core.plugins import PluginMetrics

metrics = PluginMetrics()

# Get stats for a specific plugin
stats = metrics.get_stats("weather-agent")

print(stats.requests_total)
print(stats.errors_total)
print(stats.avg_latency_ms)
```

### Prometheus Integration

Metrics are automatically exposed for Prometheus.

```text
# Exposed Metrics
plugin_requests_total{plugin="weather-agent"}
plugin_errors_total{plugin="weather-agent"}
plugin_latency_seconds{plugin="weather-agent"}
plugin_active{plugin="weather-agent"}
```

---

## Configuration

Plugins are configured via `configs/plugins.yaml`.

```yaml title="configs/plugins.yaml"
plugins:
  weather-agent:
    enabled: true
    config:
      api_key: "${WEATHER_API_KEY}"
      cache_ttl: 300
  
  analytics:
    enabled: true
    config:
      batch_size: 100
  
  legacy-plugin:
    enabled: false  # Disabled
```

### Accessing Configuration

Inherited configuration is available in the `initialize` method.

```python
class MyPlugin(Plugin):
    async def initialize(self, config: dict) -> None:
        self.api_key = config.get("api_key")
        self.cache_ttl = config.get("cache_ttl", 60)
```

---

## CLI Commands

Manage plugins directly from the command line.

```bash
# List all local plugins with readiness status
baselith plugin list

# Create a new plugin (supports --interactive wizard)
baselith plugin create my-plugin --type agent

# Comprehensive status (aligned with configs/plugins.yaml)
baselith plugin status

# Verify dependencies and environment
baselith plugin deps check my-plugin

# Target logs for a specific plugin
baselith plugin logs my-plugin

# Visualize dependency tree
baselith plugin tree

# Validate syntax and manifest
baselith plugin validate my-plugin
```

---

## Best Practices

!!! tip "Structure"
    - Use `plugin.py` as the single entry point.
    - Keep logic in separate files (`agent.py`, `handlers.py`) for maintainability.
    - Always include a `README.md` for documentation.

!!! tip "Performance"
    - Leverage [Lazy Loading](../advanced/lazy-loading.md) for heavy dependencies.
    - Implement health checks.
    - Monitor exposed metrics.

!!! tip "Security"
    - **Always** validate external inputs.
    - Use configuration for secrets; **never** hardcode API keys or credentials.
    - Implement rate limiting if you expose public APIs.
