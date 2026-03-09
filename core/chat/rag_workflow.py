"""
RAG Workflow — Full RAG pipeline as both workflow steps and an Orchestrator-compatible FlowHandler.

Refactored from the legacy AgentWorkflow/ChatAgent (LangGraph) to integrate
directly with the Orchestrator via the ``FlowHandler`` protocol.

Usage as FlowHandler:
    handler = RagWorkflowHandler(chat_service)
    orchestrator.register_handler("rag_full", handler)

Usage as step collection (backward compatible):
    workflow = RagWorkflow(chat_service)
    workflow.validate_input(state)
"""

from __future__ import annotations

from core.observability.logging import get_logger
from typing import Any, Dict, TYPE_CHECKING

from core.chat.agent_state import AgentState
from core.chat.guardrails import evaluate_guardrails
from core.chat.context import build_context_and_sources
from core.chat.prompt import build_prompt
from core.chat.reranking import rerank_hits
from core.chat.workflow_planner import BacklogPlanner
from core.chat.workflow_response import Clarifier, ResponseGenerator
from core.chat.workflow_retrieval import RetrievalPipeline
from core.chat.workflow_validation import InputValidator
from core.services.llm import get_llm_service
from core.services.vectorstore import get_vectorstore_service
from core.observability import telemetry

if TYPE_CHECKING:
    from core.chat.service import ChatService

logger = get_logger(__name__)


def _search_wrapper(query_vector, **kwargs):
    """Wrapper for vectorstore search to maintain compatibility."""
    service = get_vectorstore_service()
    return service.search(query_vector=query_vector, **kwargs)


class RagWorkflow:
    """Collection of atomic RAG pipeline steps operating on AgentState.

    Each method mutates the shared ``AgentState`` and sets ``next_action``
    so that callers can drive the pipeline step-by-step.
    """

    def __init__(self, service: "ChatService") -> None:
        self.service = service
        self.validator = InputValidator(service)
        self.retrieval = RetrievalPipeline(
            service,
            search_fn=_search_wrapper,
            rerank_fn=rerank_hits,
            build_context_fn=build_context_and_sources,
        )
        self.planner = BacklogPlanner(service)
        self.clarifier = Clarifier(service)
        self.responder = ResponseGenerator(
            service,
            build_prompt_fn=build_prompt,
            generate_response_fn=get_llm_service().generate_response,
        )

    # -- Sync steps -----------------------------------------------------------

    def validate_input(self, state: AgentState) -> None:
        """
        Validate user input and check for early exits.

        Args:
            state: Current agent state.
        """
        self.validator.validate_input(state)

    def classify_intent(self, state: AgentState) -> None:
        """
        Classify query intent and apply safety guardrails.

        Args:
            state: Current agent state.
        """
        decision = evaluate_guardrails(state.user_query)
        state.guardrail_decision = decision
        log_message = f"guardrail:{decision.action}"
        if decision.matched:
            log_message = f"{log_message}:{decision.matched}"
        if decision.action == "block":
            state.answer = decision.response or "Richiesta non consentita."
            telemetry.increment("guardrail.block")
            telemetry.increment("answers.guardrail_block")
            state.log(log_message)
            state.done = True
            state.next_action = ""
            return
        if decision.action == "fallback":
            state.answer = (
                decision.response
                or "Posso rispondere solo a domande sui documenti indicizzati."
            )
            telemetry.increment("guardrail.fallback")
            telemetry.increment("answers.guardrail_fallback")
            state.log(log_message)
            state.done = True
            state.next_action = ""
            return
        telemetry.increment("guardrail.allow")
        state.log(log_message)
        state.next_action = "prepare_query"

    def prepare_query(self, state: AgentState) -> None:
        """
        Prepare the query for retrieval.

        Args:
            state: Current agent state.
        """
        state.next_action = "load_history"

    def plan_backlog(self, state: AgentState) -> None:
        """
        Plan the next steps for the agent based on context.

        Args:
            state: Current agent state.
        """
        self.planner.plan_backlog(state)

    def request_clarification(self, state: AgentState) -> None:
        """
        Request clarification from the user.

        Args:
            state: Current agent state.
        """
        self.clarifier.request_clarification(
            state, message_builder=self._compose_clarification_message
        )

    def finalize_answer(self, state: AgentState) -> None:
        """
        Finalize the generated answer and update telemetry.

        Args:
            state: Current agent state.
        """
        self.responder.finalize_answer(state)

    # -- Async steps ----------------------------------------------------------

    async def load_history(self, state: AgentState) -> None:
        """
        Load conversation history for context.

        Args:
            state: Current agent state.
        """
        await self.retrieval.load_history(state)

    async def retrieve_documents(self, state: AgentState) -> None:
        """
        Retrieve relevant documents from the vector store.

        Args:
            state: Current agent state.
        """
        await self.retrieval.retrieve_documents(state)

    async def score_documents(self, state: AgentState) -> None:
        """
        Rerank and score retrieved documents.

        Args:
            state: Current agent state.
        """
        await self.retrieval.score_documents(state)

    async def apply_feedback(self, state: AgentState) -> None:
        """
        Apply user feedback to influence document scoring.

        Args:
            state: Current agent state.
        """
        await self.retrieval.apply_feedback(state)

    async def build_context(self, state: AgentState) -> None:
        """
        Consolidate retrieved information into a context block.

        Args:
            state: Current agent state.
        """
        await self.retrieval.build_context(state)

    async def check_cache(self, state: AgentState) -> None:
        """
        Check if a similar response is available in cache.

        Args:
            state: Current agent state.
        """
        await self.retrieval.check_cache(state)

    async def generate_answer(self, state: AgentState) -> None:
        """
        Generate a response using the LLM.

        Args:
            state: Current agent state.
        """
        await self.responder.generate_answer(state)

    # -- Internal helpers -----------------------------------------------------

    def _compose_clarification_message(self, state: AgentState) -> str:
        return self.clarifier._compose_clarification_message(state)


# Backward compatible alias
AgentWorkflow = RagWorkflow


class RagWorkflowHandler:
    """Orchestrator-compatible FlowHandler that runs the full RAG pipeline.

    This wraps :class:`RagWorkflow` behind the ``FlowHandler`` protocol so
    it can be registered as an intent handler in the Orchestrator:

        orchestrator.register_handler("rag_full", RagWorkflowHandler(service))
    """

    def __init__(self, service: "ChatService") -> None:
        self._workflow = RagWorkflow(service)

    async def handle(self, query: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Run the full RAG pipeline and return an Orchestrator result dict."""
        from core.models.chat import ChatRequest

        # Inject session_id (as conversation_id) and tenant_id from context
        request = ChatRequest(
            query=query,
            conversation_id=context.get("session_id"),
            tenant_id=context.get("tenant_id"),
        )
        state = AgentState(request=request)
        wf = self._workflow

        # 1. Validate + guardrails
        wf.validate_input(state)
        if state.done:
            return self._to_result(state)
        wf.classify_intent(state)
        if state.done:
            return self._to_result(state)

        # 2. Retrieval pipeline
        await wf.load_history(state)
        await wf.retrieve_documents(state)

        if state.clarification_reason:
            wf.request_clarification(state)
            return self._to_result(state)

        await wf.score_documents(state)
        await wf.build_context(state)
        await wf.check_cache(state)

        if state.done:
            return self._to_result(state)

        # 3. Generation
        wf.plan_backlog(state)
        await wf.generate_answer(state)
        wf.finalize_answer(state)

        return self._to_result(state)

    @staticmethod
    def _to_result(state: AgentState) -> Dict[str, Any]:
        return {
            "response": state.answer or "",
            "sources": [
                {"source": s.get("source", ""), "content": s.get("content", "")}
                for s in (state.doc_sources or [])
            ],
            "metadata": {
                "guardrail": (
                    state.guardrail_decision.action
                    if state.guardrail_decision
                    else None
                ),
                "clarification": state.clarification_reason,
            },
        }


__all__ = ["RagWorkflow", "RagWorkflowHandler", "AgentWorkflow"]
