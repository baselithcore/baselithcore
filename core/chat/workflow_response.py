"""
Workflow Response Orchestration.

Handles the final stages of the chat workflow, including clarification requests
and answer generation via LLM integration.
"""

from __future__ import annotations

from typing import List, TYPE_CHECKING

from core.chat.agent_state import AgentState
from core.chat.prompt import build_prompt
from core.services.llm import get_llm_service
from core.observability import telemetry
from core.config import get_llm_config
import inspect

OLLAMA_MODEL = get_llm_config().model

if TYPE_CHECKING:
    from core.chat.service import ChatService


class Clarifier:
    """Handle clarification responses when the RAG pipeline cannot answer."""

    def __init__(self, service: "ChatService") -> None:
        self.service = service

    def request_clarification(
        self,
        state: AgentState,
        *,
        message_builder=None,
    ) -> None:
        """
        Transitions the agent state to a clarification response.

        Args:
            state: The current agent state to modify.
            message_builder: Optional custom function to compose the clarification string.
        """
        telemetry.increment("clarification.triggered")
        if state.clarification_reason:
            telemetry.increment(f"clarification.{state.clarification_reason}")
            state.log(f"clarification:reason={state.clarification_reason}")
        telemetry.increment("answers.clarification")
        builder = message_builder or self._compose_clarification_message
        message = builder(state)
        state.answer = message
        state.done = True
        state.next_action = ""

    def _compose_clarification_message(self, state: AgentState) -> str:
        query_snippet = (state.user_query or state.request.query or "").strip()
        base_message = "I couldn't find enough information in the indexed documents"
        if query_snippet:
            base_message = f'{base_message} for the question "{query_snippet}".'
        else:
            base_message = f"{base_message}."

        tips: List[str] = [
            "add details about the department, project, or time period you're referring to",
            "specify the file name, document name, or a distinctive keyword",
            "describe the goal or problem you're trying to solve",
        ]

        if state.history_text:
            tips.append(
                "refer to a previous part of the conversation that you consider relevant"
            )

        suggestions = "\n".join(f"- {tip}" for tip in tips)
        return f"{base_message}\n\nYou can help me by:\n{suggestions}"


class ResponseGenerator:
    """LLM prompt orchestration and final answer formatting."""

    def __init__(
        self,
        service: "ChatService",
        *,
        build_prompt_fn=build_prompt,
        generate_response_fn=None,
        generate_response_stream_fn=None,
    ) -> None:
        self.service = service
        self.build_prompt_fn = build_prompt_fn
        # Use provided function or get from LLM service
        self.generate_response_fn = (
            generate_response_fn or get_llm_service().generate_response
        )
        self.generate_response_stream_fn = (
            generate_response_stream_fn or get_llm_service().generate_response_stream
        )

    async def generate_answer(self, state: AgentState) -> None:
        """
        Orchestrates prompt building and synchronous LLM response generation.

        Args:
            state: The current agent state to populate with the LLM answer.
        """
        prompt = self.build_prompt_fn(
            state.user_query,
            state.context,
            state.history_text,
            project_plan=state.plugin_data.get("project_plan"),
        )
        if hasattr(
            self.generate_response_fn, "__await__"
        ) or inspect.iscoroutinefunction(self.generate_response_fn):
            state.answer = await self.generate_response_fn(prompt, model=OLLAMA_MODEL)  # type: ignore[misc]
        else:
            import asyncio

            state.answer = await asyncio.to_thread(
                self.generate_response_fn, prompt, model=OLLAMA_MODEL
            )
        state.next_action = "finalize_answer"

    async def generate_answer_stream(self, state: AgentState):
        """Generates answer using streaming, yielding chunks."""
        prompt = self.build_prompt_fn(
            state.user_query,
            state.context,
            state.history_text,
            project_plan=state.plugin_data.get("project_plan"),
        )

        full_answer = []
        import inspect

        if inspect.isasyncgenfunction(self.generate_response_stream_fn):
            async for chunk in self.generate_response_stream_fn(
                prompt, model=OLLAMA_MODEL
            ):
                full_answer.append(chunk)
                yield chunk
        else:
            # Sync iterator — collect in a thread to avoid blocking the event loop
            import asyncio

            chunks = await asyncio.to_thread(
                lambda: list(
                    self.generate_response_stream_fn(prompt, model=OLLAMA_MODEL)  # type: ignore[arg-type]
                )
            )
            for chunk in chunks:
                full_answer.append(chunk)
                yield chunk

        state.answer = "".join(full_answer)
        state.next_action = "finalize_answer"

    def finalize_answer(self, state: AgentState) -> None:
        """
        Performs final state transitions and telemetry recording after answer generation.

        Args:
            state: The current agent state to finalize.
        """
        answer = state.answer

        if isinstance(answer, str):
            # Legacy: answer = self.service._finalize_answer_state(state, answer)
            # RAGAgent/Orchestrator now handle final composition.
            pass
        telemetry.increment("answers.generated")

        state.done = True
        state.next_action = ""


__all__ = ["Clarifier", "ResponseGenerator"]
