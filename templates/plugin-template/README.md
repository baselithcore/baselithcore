# Plugin Template

Minimal template for creating new plugins for the BaselithCore system.

## Quick Start

```bash
# 1. Copy the template
cp -r templates/plugin-template plugins/my-plugin

# 2. Rename and customize
cd plugins/my-plugin
# Edit plugin.py with your specific logic
```

## Structure

```txt
plugin-template/
├── plugin.py        # Plugin implementation (REQUIRED)
├── models.py        # Data models
├── router.py        # API Router (optional)
└── tests/
    └── test_plugin.py
```

## Plugin Interface

Every plugin must implement at least `BasePlugin` (or `AgentPlugin`, `RouterPlugin` depending on functionality):

```python
from core.plugins import BasePlugin

class MyPlugin(BasePlugin):
    name = "my-plugin"
    version = "1.0.0"
    
    def initialize(self, config: dict) -> None:
        """Called during system startup."""
        pass
```

## Optional Capabilities

### AgentPlugin

For plugins providing agents:

```python
from core.plugins import AgentPlugin

class MyAgentPlugin(AgentPlugin):
    def create_agent(self, service, **kwargs):
        return MyAgent(service)
```

### GraphPlugin

For plugins extending the knowledge graph:

```python
from core.plugins import GraphPlugin

class MyGraphPlugin(GraphPlugin):
    def register_entity_types(self) -> list[dict]:
        return [{"type": "my_entity", "display": "My Entity"}]
```

### RouterPlugin

For plugins adding API endpoints (FastAPI):

```python
from core.plugins import RouterPlugin

class MyRouterPlugin(RouterPlugin):
    def get_router(self):
        return self.router
```

## Configuration

Plugins are configured via `configs/plugins.yaml`:

```yaml
my-plugin:
  enabled: true
  config:
    option1: value1
```

## Testing

```bash
pytest plugins/my-plugin/tests/
```
