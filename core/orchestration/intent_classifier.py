"""
Generic Intent Classifier Module.

This module provides the intelligence layer for routing user messages.
It supports a hybrid hierarchy of classification strategies:
1. LLM-based: High-precision semantic matching with confidence scoring.
2. Keyword-based: High-speed pattern matching for exact or frequent phrases.
3. Default: Resilient fallback to the system's primary capability (e.g., RAG).

Dynamic intents are automatically loaded from registered plugins, allowing
the system to expand its behavioral repertoire at runtime.
"""

from __future__ import annotations
import json
from core.observability.logging import get_logger
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from core.plugins import PluginRegistry

logger = get_logger(__name__)


@dataclass
class ClassificationResult:
    """
    Structured output of the classification process.

    Attributes:
        intent: The identified action or category (e.g., "qa_docs", "weather").
        confidence: Numerical score (0.0 to 1.0) of the classification accuracy.
        method: The strategy that produced this result ("llm", "keyword", "default").
        alternatives: Optional list of other possible intents if ambiguity exists.
    """

    intent: str
    confidence: float
    method: str
    alternatives: Optional[List[Dict]] = None


# Prompt template for semantic classification.
CLASSIFICATION_PROMPT = """Classify the user's intent from their message.

Available intents:
{intents_list}

User message: "{query}"

Respond ONLY with a JSON object in this exact format:
{{
    "intent": "<intent_name>",
    "confidence": <0.0-1.0>,
    "reasoning": "<brief explanation>"
}}

Choose the most appropriate intent. If unsure, use lower confidence."""


class IntentClassifier:
    """
    Multi-strategy Intent Classifier.

    This class coordinates the extraction of meaning from user input.
    It maintains a priority-based pipeline:
    LLM (if enabled and confidence > threshold) -> Keyword match -> Default.

    Extensibility is handled by lazy-loading patterns from the `PluginRegistry`.
    """

    DEFAULT_INTENT = "qa_docs"
    DEFAULT_CONFIDENCE_THRESHOLD = 0.6

    def __init__(
        self,
        llm_service: Optional[Any] = None,
        plugin_registry: Optional["PluginRegistry"] = None,
        default_intent: str = DEFAULT_INTENT,
        telemetry_enabled: bool = False,
        llm_enabled: bool = True,
        confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    ):
        """
        Initialize the intent classification engine.

        Args:
            llm_service: Service used to run semantic classification prompts.
            plugin_registry: Registry to pull dynamic intents from.
            default_intent: The routing used when no specific intent is found.
            telemetry_enabled: If True, records hits for observability.
            llm_enabled: Global toggle for LLM-based expensive classification.
            confidence_threshold: Minimum score required to accept an LLM result.
        """
        self.plugin_registry = plugin_registry
        self.default_intent = default_intent
        self.telemetry_enabled = telemetry_enabled
        self.llm_enabled = llm_enabled
        self.confidence_threshold = confidence_threshold
        self.llm_service = llm_service
        self._plugin_intents_loaded = False
        self._plugin_intent_patterns: Dict[str, Dict] = {}

        # Fallback logic for LLM service acquisition.
        if self.llm_enabled and not self.llm_service:
            try:
                from core.services.llm import get_llm_service

                self.llm_service = get_llm_service()
            except Exception as e:
                logger.warning(f"Could not load LLM service for classifier: {e}")
                self.llm_enabled = False

    def _load_plugin_intents(self) -> None:
        """
        Pull intent definitions from active plugins. Loaded lazily on first query.
        """
        if self._plugin_intents_loaded or not self.plugin_registry:
            return

        try:
            all_patterns = self.plugin_registry.get_all_intent_patterns()
            for intent_name, pattern_def in all_patterns.items():
                self._plugin_intent_patterns[intent_name] = pattern_def

            if all_patterns:
                logger.info(f"Loaded {len(all_patterns)} intent patterns from plugins")
        except Exception as e:
            logger.warning(f"Failed to load plugin intents: {e}")
        finally:
            self._plugin_intents_loaded = True

    def register_intent(
        self,
        intent_name: str,
        patterns: list[str],
        priority: int = 0,
        description: str = "",
    ) -> None:
        """
        Dynamically register a new intent pattern.

        Args:
            intent_name: Identifier for the intent (must match a handler).
            patterns: List of keyword patterns to trigger this intent.
            priority: Order of precedence for keyword matching (higher first).
            description: Semantic description passed to the LLM.
        """
        self._plugin_intent_patterns[intent_name] = {
            "patterns": patterns,
            "priority": priority,
            "description": description or f"Handle {intent_name} requests",
        }
        logger.debug(f"Registered intent: {intent_name} with {len(patterns)} patterns")

    async def classify(self, text: str) -> str:
        """
        Identify the intent ID for a given input string.

        Args:
            text: Raw user input.

        Returns:
            str: The identifier of the chosen intent.
        """
        result = await self.classify_with_confidence(text)
        return result.intent

    async def classify_with_confidence(self, text: str) -> ClassificationResult:
        """
        Perform a full tiered classification with confidence scoring.

        Logic:
        1. LLM: If enabled, ask the model to pick from all available intents.
        2. Keyword: If LLM is not confident or disabled, check pattern matches.
        3. Default: Fallback if no specific intent is detected.

        Args:
            text: Raw user input.

        Returns:
            ClassificationResult: Detailed outcome including method used.
        """
        # Ensure latest plugin intents are available.
        self._load_plugin_intents()

        # Strategy 1: LLM semantic analysis.
        if self.llm_enabled and self._plugin_intent_patterns:
            llm_result = await self._classify_with_llm(text)
            if llm_result and llm_result.confidence >= self.confidence_threshold:
                self._record_telemetry(llm_result.intent)
                logger.debug(
                    f"LLM classified intent: {llm_result.intent} "
                    f"(confidence: {llm_result.confidence:.2f})"
                )
                return llm_result

        # Strategy 2: Keyword pattern matching.
        keyword_result = self._classify_with_keywords(text)
        if keyword_result:
            self._record_telemetry(keyword_result.intent)
            logger.debug(f"Keyword matched intent: {keyword_result.intent}")
            return keyword_result

        # Strategy 3: Default fallback.
        self._record_telemetry(self.default_intent)
        logger.debug(f"Using default intent: {self.default_intent}")
        return ClassificationResult(
            intent=self.default_intent,
            confidence=0.5,
            method="default",
        )

    async def _classify_with_llm(self, text: str) -> Optional[ClassificationResult]:
        """
        Execute an LLM call to categorize input text.

        Uses the classification prompt to ask the model to pick from a list
        of registered intents based on the provided query.

        Args:
            text: Raw user input text.

        Returns:
            Optional[ClassificationResult]: The model's classification if successful.
        """
        if not self.llm_service:
            return None

        # Build descriptions for semantic guidance.
        intents_list = "\n".join(
            f"- {name}: {info.get('description', 'No description')}"
            for name, info in self._plugin_intent_patterns.items()
        )
        intents_list += (
            f"\n- {self.default_intent}: General Q&A and documentation queries"
        )

        prompt = CLASSIFICATION_PROMPT.format(
            intents_list=intents_list,
            query=text[:500],  # Truncate for prompt safety.
        )

        try:
            # Execute LLM generation (dual-support for sync/async service methods).
            if hasattr(self.llm_service, "generate_response_async"):
                response = await self.llm_service.generate_response_async(
                    prompt, json=True
                )
            else:
                response = await self.llm_service.generate_response(prompt, json=True)

            result = json.loads(response)

            intent = result.get("intent", self.default_intent)
            confidence = float(result.get("confidence", 0.5))

            # Validate that the LLM didn't hallucinate an unknown intent.
            valid_intents = set(self._plugin_intent_patterns.keys())
            valid_intents.add(self.default_intent)
            if intent not in valid_intents:
                logger.warning(f"LLM returned unknown intent: {intent}")
                return None

            return ClassificationResult(
                intent=intent,
                confidence=min(1.0, max(0.0, confidence)),
                method="llm",
            )

        except Exception as e:
            logger.warning(f"LLM classification failed: {e}")
            return None

    def _classify_with_keywords(self, text: str) -> Optional[ClassificationResult]:
        """
        Search for exact or substring matches in user input.

        Iterates through all registered intent patterns to find a match
        based on simple keyword identification.

        Args:
            text: Raw user input text.

        Returns:
            Optional[ClassificationResult]: A keyword-based match if found.
        """
        text_lower = text.lower()

        # Evaluate intents by priority.
        sorted_intents = sorted(
            self._plugin_intent_patterns.items(),
            key=lambda x: x[1].get("priority", 0),
            reverse=True,
        )

        for intent_name, pattern_def in sorted_intents:
            patterns = pattern_def.get("patterns", [])
            if any(pattern.lower() in text_lower for pattern in patterns):
                return ClassificationResult(
                    intent=intent_name,
                    confidence=0.8,  # Hardcoded confidence for pattern match.
                    method="keyword",
                )

        return None

    def _record_telemetry(self, intent: str) -> None:
        """
        Increment hit count for a specific intent in observability.

        Records the usage of an intent for monitoring and analytics.

        Args:
            intent: The name of the intent to track.
        """
        if not self.telemetry_enabled:
            return

        try:
            from core.observability import telemetry

            telemetry.increment(f"orchestrator.intent.{intent}")
        except ImportError:
            pass

    def get_available_intents(self) -> List[str]:
        """
        List all IDs currently handled by the classifier.

        Returns:
            List[str]: A list of all unique registered intent names.
        """
        self._load_plugin_intents()
        intents = list(self._plugin_intent_patterns.keys())
        if self.default_intent not in intents:
            intents.append(self.default_intent)
        return intents
