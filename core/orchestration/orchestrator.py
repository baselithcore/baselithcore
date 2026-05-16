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
from .limits import LoopLimits
from .contract import AgentContract, ContractValidator
from .autonomy import AutonomyPolicy

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

    def _register_builtin_handler(self, intent: str, loader) -> None:
        """Register a built-in handler without letting optional dependencies break init."""
        try:
            self.register_handler(intent, loader())
        except ImportError:
            return
        except Exception as exc:
            logger.warning(
                "Failed to initialize built-in handler for intent '%s': %s",
                intent,
                exc,
            )

    def __init__(
        self,
        intent_classifier: Optional[IntentClassifier] = None,
        plugin_registry: Optional["PluginRegistry"] = None,
        default_intent: str = "qa_docs",
        memory_manager: Optional["AgentMemory"] = None,
        human_intervention: Optional["HumanIntervention"] = None,
        feedback_collector: Optional["FeedbackCollector"] = None,
        llm_service: Optional[Any] = None,
        loop_limits: Optional[LoopLimits] = None,
        agent_contract: Optional[AgentContract] = None,
        autonomy_policy: Optional[AutonomyPolicy] = None,
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
            loop_limits: Per-request iteration/cost/tool-call caps. Defaults to
                production-safe values when omitted.
            agent_contract: Optional machine-readable contract that gates tool
                calls and output shape.
            autonomy_policy: Optional autonomy spectrum policy. Defaults to
                ``SUPERVISED`` when omitted.
        """
        self.plugin_registry = plugin_registry
        self.default_intent = default_intent
        self.memory_manager = memory_manager
        self.human_intervention = human_intervention
        self.feedback_collector = feedback_collector
        self.loop_limits = loop_limits or LoopLimits()
        self.agent_contract = agent_contract
        self.contract_validator = (
            ContractValidator(agent_contract) if agent_contract else None
        )
        self.autonomy_policy = autonomy_policy or AutonomyPolicy()

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
            self._register_builtin_handler(
                default_intent,
                lambda: __import__(
                    "core.orchestration.handlers.rag",
                    fromlist=["StandardRagHandler"],
                ).StandardRagHandler(),
            )
            if default_intent in self._flow_handlers:
                logger.info(
                    "Registered StandardRagHandler for default intent 'qa_docs'"
                )

        # 3. Native Intelligence: Register built-in specialized handlers.
        # These are conditionally loaded based on core module availability.
        self._register_builtin_handler(
            "complex_reasoning",
            lambda: __import__(
                "core.orchestration.handlers.reasoning",
                fromlist=["ReasoningHandler"],
            ).ReasoningHandler(),
        )
        self._register_builtin_handler(
            "vision_analysis",
            lambda: __import__(
                "core.orchestration.handlers.vision",
                fromlist=["VisionHandler"],
            ).VisionHandler(),
        )
        self._register_builtin_handler(
            "multimodal_reasoning",
            lambda: __import__(
                "core.orchestration.handlers.multimodal_reasoning",
                fromlist=["MultiModalReasoningHandler"],
            ).MultiModalReasoningHandler(),
        )
        self._register_builtin_handler(
            "collaborative_task",
            lambda: __import__(
                "core.orchestration.handlers.swarm_handler",
                fromlist=["SwarmHandler"],
            ).SwarmHandler(),
        )
        self._register_builtin_handler(
            "scenario_simulation",
            lambda: __import__(
                "core.orchestration.handlers.simulation_handler",
                fromlist=["SimulationHandler"],
            ).SimulationHandler(),
        )

        # Synchronize classifiers with the registered handlers.
        self._register_core_intent_patterns()
