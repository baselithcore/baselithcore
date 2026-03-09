"""
Plugin entry point for Document Analyzer.
"""

from typing import Any, Dict, List, Optional

from core.plugins.agent_plugin import AgentPlugin
from core.plugins.interface import PluginMetadata
from .main import DocumentAnalyzer


class DocumentAnalyzerPlugin(AgentPlugin):
    """Plugin wrapping the Document Analyzer Agent."""

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="document-analyzer",
            version="0.1.0",
            description="Document entity and relationship extraction assistant",
            author="Baselith Team",
            required_resources=["llm"],
            tags=["nlp", "analysis", "entities"]
        )

    def __init__(self):
        super().__init__()
        self._agent: Optional[DocumentAnalyzer] = None

    async def initialize(self, config: Dict[str, Any]) -> None:
        await super().initialize(config)
        self._agent = DocumentAnalyzer()
        # Note: DocumentAnalyzer should ideally have an initialize method too
        # For now we assume its constructor is sufficient or add initialization here if needed.

    async def shutdown(self) -> None:
        await super().shutdown()

    def create_agent(self, service: Any, **kwargs) -> Any:
        return self._agent

    def get_agents(self) -> List[Any]:
        return [self._agent] if self._agent else []

    def get_intent_patterns(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "analyze_doc",
                "patterns": ["analyze document", "extract entities", "who is in this file"],
                "priority": 15
            }
        ]
