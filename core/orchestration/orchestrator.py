"""
Central Behavioral Coordination and Intent Routing.

Acts as the 'Brain' of the Baselith-Core. Orchestrates the end-to-end
lifecycle of a user request: from intent classification and context
retrieval to parallel execution of specialized flow/stream handlers.
Implements a modular, mixin-based architecture for high extensibility.
"""

from __future__ import annotations
from core.observability.logging import get_logger
from typing import Any, Dict, Optional, TYPE_CHECKING

from .intent_classifier import IntentClassifier
from .protocols import FlowHandler, StreamHandler

from .mixins.intent import IntentMixin
from .mixins.handlers import HandlersMixin
from .mixins.execution import ExecutionMixin

if TYPE_CHECKING:
    from core.plugins import PluginRegistry
    from core.memory import AgentMemory
    from core.human import HumanIntervention
    from core.learning import FeedbackCollector

logger = get_logger(__name__)


class Orchestrator(IntentMixin, HandlersMixin, ExecutionMixin):
    """
    Primary Coordination Engine for Agentic Operations.

    Facilitates the seamless integration of memory, plugins, and LLM
    reasoning. Routes user prompts to the most appropriate handler
    (RAG, Vision, Swarm, etc.) based on classified intent, while
    maintaining observability and human-in-the-loop safety boundaries.
    """

    def __init__(
        self,
        intent_classifier: Optional[IntentClassifier] = None,
        plugin_registry: Optional["PluginRegistry"] = None,
        default_intent: str = "qa_docs",
        memory_manager: Optional["AgentMemory"] = None,
        human_intervention: Optional["HumanIntervention"] = None,
        feedback_collector: Optional["FeedbackCollector"] = None,
        llm_service: Optional[Any] = None,
    ) -> None:
        """
        Initialize the system's main coordinator.

        Args:
            intent_classifier: Logic for mapping text to intents.
                Defaults to an LLM-powered classifier if None.
            plugin_registry: Source of truth for active plugins and their handlers.
            default_intent: Fallback routing (typically standard RAG).
            memory_manager: Interface for long-term and short-term memory retrieval.
            human_intervention: Controller for managing tasks requiring user approval.
            feedback_collector: Component for tracking performance metrics and reinforcement.
            llm_service: Explicit LLM provider used during classification.
        """
        self.plugin_registry = plugin_registry
        self.default_intent = default_intent
        self.memory_manager = memory_manager
        self.human_intervention = human_intervention
        self.feedback_collector = feedback_collector

        # Initialize intent classifier: the first stage of the pipeline.
        self.intent_classifier = intent_classifier or IntentClassifier(
            plugin_registry=plugin_registry,
            default_intent=default_intent,
            llm_service=llm_service,
        )

        # Handler registries for specialized execution logic.
        self._flow_handlers: Dict[str, FlowHandler] = {}
        self._stream_handlers: Dict[str, StreamHandler] = {}

        # 1. Extensibility: Load handlers provided by registered plugins.
        self._load_plugin_handlers()

        # 2. Core Logic: Register the default RAG handler if not overridden.
        if default_intent not in self._flow_handlers and default_intent == "qa_docs":
            try:
                from core.orchestration.handlers.rag import StandardRagHandler

                self.register_handler(default_intent, StandardRagHandler())
                logger.info(
                    "Registered StandardRagHandler for default intent 'qa_docs'"
                )
            except ImportError:
                logger.warning("StandardRagHandler not available, checking plugins...")
                pass

        # 3. Native Intelligence: Register built-in specialized handlers.
        # These are conditionally loaded based on core module availability.

        try:
            # Multi-step logical reasoning engine.
            from core.orchestration.handlers.reasoning import ReasoningHandler

            self.register_handler("complex_reasoning", ReasoningHandler())
        except ImportError:
            pass

        try:
            # Image and visual data analysis handler.
            from core.orchestration.handlers.vision import VisionHandler

            self.register_handler("vision_analysis", VisionHandler())
        except ImportError:
            pass

        try:
            # Advanced handler for multi-modal context (Text + Image logic).
            from core.orchestration.handlers.multimodal_reasoning import (
                MultiModalReasoningHandler,
            )

            self.register_handler("multimodal_reasoning", MultiModalReasoningHandler())
        except ImportError:
            pass

        try:
            # Multi-agent collaborative swarm handler.
            from core.orchestration.handlers.swarm_handler import SwarmHandler

            self.register_handler("collaborative_task", SwarmHandler())
        except ImportError:
            pass

        # Synchronize classifiers with the registered handlers.
        self._register_core_intent_patterns()
