"""
Plugin entry point for FAQ Agent.
"""

from typing import Any, Dict, List, Optional

from core.plugins.agent_plugin import AgentPlugin
from core.plugins.interface import PluginMetadata
from .agent import FAQAgent


class FAQPlugin(AgentPlugin):
    """Plugin wrapping the FAQ Agent."""

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="faq-reference",
            version="0.1.0",
            description="Reference FAQ Agent Plugin",
            author="Baselith Team",
            required_resources=["llm"],
            tags=["demo", "faq", "reference"]
        )

    def __init__(self):
        super().__init__()
        self._faq_agent: Optional[FAQAgent] = None

    async def initialize(self, config: Dict[str, Any]) -> None:
        """Async initialization following the manifesto."""
        await super().initialize(config)
        
        # Config can provide the KB
        kb = self.get_config("kb", {
            "what is this?": "A reference agent implementation.",
            "who are you?": "I am the FAQ Agent."
        })
        
        self._faq_agent = FAQAgent(knowledge_base=kb)
        
        # Note: If this agent needed standard services, 
        # it would use resolve(LLMServiceProtocol) here.
        
        await self._faq_agent.initialize()

    async def shutdown(self) -> None:
        """Clean shutdown."""
        if self._faq_agent:
            await self._faq_agent.shutdown()
        await super().shutdown()

    def create_agent(self, service: Any, **kwargs) -> Any:
        return self._faq_agent

    def get_agents(self) -> List[Any]:
        return [self._faq_agent] if self._faq_agent else []

    def get_intent_patterns(self) -> List[Dict[str, Any]]:
        return [{
            "name": "faq",
            "patterns": ["what is", "who are", "help"],
            "priority": 10
        }]
