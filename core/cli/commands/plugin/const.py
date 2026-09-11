"""
Constants and templates for plugin commands.
"""

# Plugin creation templates
PLUGIN_TEMPLATE = {
    "agent": {
        "manifest.yaml": """name: {name}
version: 0.1.0
description: A custom agent plugin for {name}
author: Baselith User
license: LicenseRef-Proprietary
category: AI
readiness: alpha
tenancy: shared
min_core_version: 0.31.0
entrypoint: __init__.py
plugin_dependencies: {{}}
required_resources: []
optional_resources: []
python_dependencies: []
frontend: null
health_endpoint: null
environment_variables: []
tags:
- agent
- {name}
""",
        "__init__.py": '''"""
{name} Plugin.
"""
from .plugin import {class_name}Plugin

__all__ = ["{class_name}Plugin"]
''',
        "plugin.py": '''"""
{class_name} Plugin implementation.
"""
from typing import Any, Dict, List, Optional
from core.plugins.agent_plugin import AgentPlugin
from .agent import {class_name}Agent

class {class_name}Plugin(AgentPlugin):
    """Plugin providing the {class_name} Agent."""

    async def initialize(self, config: Dict[str, Any]) -> None:
        """Initialize the plugin."""
        await super().initialize(config)
        # Setup resources if needed

    def create_agent(self, service: Any, **kwargs) -> {class_name}Agent:
        """Factory method for the agent."""
        return {class_name}Agent(
            agent_id=f"{name}-agent",
            config=self._config
        )

    def get_agents(self) -> List[Any]:
        return []
''',
        "agent.py": '''"""
{class_name} Agent implementation.
"""
from typing import Any, Dict, Optional
from core.observability.logging import get_logger
from core.lifecycle import LifecycleMixin, AgentState
from core.orchestration.protocols import AgentProtocol

logger = get_logger(__name__)

class {class_name}Agent(LifecycleMixin, AgentProtocol):
    """
    {class_name} agent implementation.
    """
    
    def __init__(self, agent_id: str, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__()
        self.agent_id = agent_id
        self.config = config or {{}}
    
    async def _do_startup(self) -> None:
        """Handle agent startup."""
        logger.info(f"Agent {name} starting up...")

    async def _do_shutdown(self) -> None:
        """Handle agent shutdown."""
        logger.info(f"Agent {name} shutting down...")

    async def execute(self, input: str, context: Optional[Dict[str, Any]] = None) -> str:
        """
        Execute agent logic.
        """
        if self.state != AgentState.READY:
            return f"Agent not ready (State: {{self.state}})"
            
        return f"Hello from {class_name}Agent! I received: {{input}}"

__all__ = ["{class_name}Agent"]
''',
    },
    "router": {
        "manifest.yaml": """name: {name}
version: 0.1.0
description: A custom router plugin for {name}
author: Baselith User
license: LicenseRef-Proprietary
category: Utilities
readiness: alpha
tenancy: shared
min_core_version: 0.31.0
entrypoint: __init__.py
plugin_dependencies: {{}}
required_resources: []
optional_resources: []
python_dependencies: []
frontend: null
health_endpoint: /{name}/health
environment_variables: []
tags:
- router
- {name}
""",
        "__init__.py": '''"""
{name} Plugin.
"""
from .router import router
from .plugin import {class_name}Plugin

__all__ = ["router", "{class_name}Plugin"]
''',
        "plugin.py": '''"""
{class_name} Router Plugin implementation.
"""
from typing import Any, Dict, Optional
from core.plugins.router_plugin import RouterPlugin
from fastapi import APIRouter
from .router import router

class {class_name}Plugin(RouterPlugin):
    """Plugin providing the {class_name} API endpoints."""

    async def initialize(self, config: Dict[str, Any]) -> None:
        await super().initialize(config)

    def create_router(self) -> APIRouter:
        return router
''',
        "router.py": '''"""
{class_name} Router implementation.
"""
from fastapi import APIRouter

router = APIRouter(prefix="/{name}", tags=["{name}"])

@router.get("/")
async def root():
    """Root endpoint."""
    return {{"plugin": "{name}", "status": "ok"}}

@router.get("/health")
async def health():
    """Health check endpoint."""
    return {{"healthy": True}}
''',
    },
    "graph": {
        "manifest.yaml": """name: {name}
version: 0.1.0
description: A custom graph schema plugin for {name}
author: Baselith User
license: LicenseRef-Proprietary
category: Knowledge
readiness: alpha
tenancy: shared
min_core_version: 0.31.0
entrypoint: __init__.py
plugin_dependencies: {{}}
required_resources:
- graph
optional_resources: []
python_dependencies: []
frontend: null
health_endpoint: null
environment_variables: []
tags:
- graph
- {name}
""",
        "__init__.py": '''"""
{name} Graph Plugin.
"""
from .plugin import {class_name}Plugin

__all__ = ["{class_name}Plugin"]
''',
        "plugin.py": '''"""
{class_name} Graph Plugin implementation.
"""
from typing import Any, Dict, List
from core.plugins.graph_plugin import GraphPlugin

class {class_name}Plugin(GraphPlugin):
    """Plugin extending the Graph Schema."""

    async def initialize(self, config: Dict[str, Any]) -> None:
        await super().initialize(config)

    def register_entity_types(self) -> List[Dict[str, Any]]:
        """Register custom entity types."""
        return [
            {{
                "type": "{name}_entity",
                "display_name": "{class_name} Entity",
                "schema": {{
                    "custom_field": "str"
                }},
                "icon": "box"
            }}
        ]

    def register_relationship_types(self) -> List[Dict[str, Any]]:
        """Register custom relationship types."""
        return [
            {{
                "type": "RELATES_TO_{name}".upper(),
                "source_types": ["{name}_entity"],
                "target_types": ["{name}_entity"],
                "bidirectional": True
            }}
        ]
''',
    },
}
