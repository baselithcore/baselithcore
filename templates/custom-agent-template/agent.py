"""
MyAgent - Custom Agent Template.

Copy this file and rename the class to match your agent's purpose.
Replace all 'MyAgent' references with your agent's name.
"""

from typing import Any, Dict, Optional
from pathlib import Path
from core.observability.logging import get_logger

from core.lifecycle import LifecycleMixin, AgentState
from core.orchestration.protocols import AgentProtocol

logger = get_logger(__name__)


class MyAgent(LifecycleMixin, AgentProtocol):
    """
    Custom agent template.

    Replace this docstring with a description of your agent's purpose.
    """

    def __init__(self, agent_id: str, llm_service: Any = None, tools: list = None):
        """
        Initialize agent.

        Args:
            agent_id: Unique identifier for this agent instance.
            llm_service: Optional LLM service (injected via DI in production).
            tools: Optional list of tool callables.
        """
        super().__init__()
        self.agent_id = agent_id
        self.llm_service = llm_service
        self.tools = tools or []
        self.system_prompt = ""

    async def _do_startup(self) -> None:
        """Load prompts and tools during startup."""
        self.system_prompt = self._load_system_prompt()
        logger.info(f"Agent {self.agent_id} initialized and ready.")

    def _load_system_prompt(self) -> str:
        """Load system prompt from file."""
        prompt_path = Path(__file__).parent / "prompts" / "system.md"
        if prompt_path.exists():
            return prompt_path.read_text()
        return "You are a helpful assistant."

    async def execute(self, input: str, context: Optional[Dict[str, Any]] = None) -> str:
        """
        Process user message and generate response.

        Args:
            input: The user's input message.
            context: Optional context dictionary (e.g., history, tenant_id).

        Returns:
            The agent's response string.
        """
        if self.state != AgentState.READY:
            return f"Agent not ready (State: {self.state})"

        context = context or {}

        # Build prompt
        full_prompt = self._build_prompt(input, context)

        # Call LLM
        response = await self._call_llm(full_prompt)

        return response.strip()

    def _build_prompt(self, message: str, context: dict) -> str:
        """Build the full prompt for the LLM."""
        history = context.get("history", [])
        history_text = "\n".join(
            f"{m['role']}: {m['content']}"
            for m in history[-5:]
        )

        return f"""{self.system_prompt}

## Conversation History
{history_text}

## User Message
{message}

## Your Response
"""

    async def _call_llm(self, prompt: str) -> str:
        """Call the LLM service."""
        if self.llm_service:
            return await self.llm_service.generate(prompt)
        # Fallback for development
        return f"[Response to: {prompt[:50]}...]"

    def get_tools(self) -> list:
        """Get available tools."""
        return self.tools

    def add_tool(self, tool: callable) -> None:
        """Add a tool to the agent."""
        self.tools.append(tool)
