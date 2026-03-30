"""
${{ values.pluginName }} — BaselithCore plugin entry point.

Dogma I  — Separation of Concerns: this file wires together the components
             declared in agent.py and router.py. No business logic here.
Dogma II — DI First: heavy dependencies are injected via the config dict
             passed to initialize(), not imported at module level.
Dogma III— Async Everything: initialize() and shutdown() are async.
"""

from typing import Any, Dict, List

from core.observability.logging import get_logger
{% if values.includeAgent %}
from core.plugins.agent_plugin import AgentPlugin
{% endif %}
{% if values.includeRouter %}
from core.plugins.router_plugin import RouterPlugin
{% endif %}
{% if not values.includeAgent and not values.includeRouter %}
from core.plugins.interface import Plugin as _PluginBase
{% endif %}

logger = get_logger(__name__)

{# Compute the class name from the plugin slug #}
{%- set className = values.pluginName | replace("-", " ") | title | replace(" ", "") + "Plugin" %}


class {{ className }}(
    {%- if values.includeAgent %}AgentPlugin{% if values.includeRouter %}, {% endif %}{% endif %}
    {%- if values.includeRouter %}RouterPlugin{% endif %}
    {%- if not values.includeAgent and not values.includeRouter %}_PluginBase{% endif %}
):
    """${{ values.description }}"""

    def __init__(self) -> None:
        super().__init__()

    async def initialize(self, config: Dict[str, Any]) -> None:
        await super().initialize(config)
        # TODO: Initialise plugin-specific resources here.
        logger.info("Plugin '${{ values.pluginName }}' initialised")

    async def shutdown(self) -> None:
        # TODO: Release plugin-specific resources here.
        await super().shutdown()

    def get_agents(self) -> List[Any]:
        """Return agents provided by this plugin."""
{% if values.includeAgent %}
        from .agent import {{ values.pluginName | replace("-", " ") | title | replace(" ", "") }}Agent
        return [{{ values.pluginName | replace("-", " ") | title | replace(" ", "") }}Agent(config=self._config)]
{% else %}
        return []
{% endif %}

    def get_routers(self) -> List[Any]:
        """Return FastAPI routers provided by this plugin."""
{% if values.includeRouter %}
        from .router import router
        return [router]
{% else %}
        return []
{% endif %}


# Required by PluginLoader to discover the plugin entry point.
plugin = {{ className }}()
