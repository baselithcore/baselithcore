"""
Plugin entry point for Research Assistant.
"""

from typing import Any, Dict, List, Optional

from core.plugins.agent_plugin import AgentPlugin
from core.plugins.interface import PluginMetadata
from .main import ResearchAssistantAgent


from core.services.llm import LLMService

class ResearchAssistantPlugin(AgentPlugin):
    """Plugin wrapping the Research Assistant Agent."""

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="research-assistant",
            version="0.1.0",
            description="Scientific paper analysis assistant",
            author="Baselith Team",
            required_resources=["llm"],
            tags=["research", "analysis", "papers"]
        )

    def __init__(self):
        super().__init__()
        self._agent: Optional[ResearchAssistantAgent] = None

    async def initialize(self, config: Dict[str, Any]) -> None:
        """Initialize plugin and its agent."""
        await super().initialize(config)
        # Assuming ResearchAssistantAgent can take an LLM service
        self._agent = ResearchAssistantAgent()
        await self._agent.initialize()

    async def shutdown(self) -> None:
        """Shutdown plugin and cleanup resources."""
        if self._agent:
            await self._agent.shutdown()
        await super().shutdown()

    def create_agent(self, service: Any, **kwargs) -> Any:
        return self._agent

    def get_agents(self) -> List[Any]:
        return [self._agent] if self._agent else []

    def get_intent_patterns(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "research_search",
                "patterns": ["search papers", "find paper about", "look up study"],
                "priority": 15
            },
            {
                "name": "research_synthesize",
                "patterns": ["synthesize", "summarize studies", "themes in papers"],
                "priority": 15
            }
        ]
