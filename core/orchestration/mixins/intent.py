"""Intent Mixin for Orchestrator."""

from typing import TYPE_CHECKING
import asyncio

if TYPE_CHECKING:
    from core.orchestration.intent_classifier import IntentClassifier


class IntentMixin:
    """Mixin for intent classification logic."""

    intent_classifier: "IntentClassifier"

    def _register_core_intent_patterns(self) -> None:
        """
        Register built-in intent patterns for the orchestrator.

        Configures the keyword and semantic patterns for native capabilities
        like reasoning, vision, multimodal analysis, and swarm collaboration.
        """
        # Complex reasoning patterns
        self.intent_classifier.register_intent(
            intent_name="complex_reasoning",
            patterns=[
                # Italian patterns (backward compatibility)
                "ragiona",
                "analizza passo",
                "pensaci",
                "rifletti",
                "ragionamento",
                "problema complesso",
                "pensa a voce alta",
                # English
                "step by step",
                "reason",
                "analyze step",
                "think about",
                "reflect",
                "reasoning",
                "complex problem",
                "think aloud",
            ],
            priority=10,
            description="Complex multi-step reasoning using Tree of Thoughts",
        )

        # Vision analysis patterns
        self.intent_classifier.register_intent(
            intent_name="vision_analysis",
            patterns=[
                # Italian patterns (backward compatibility)
                "cosa vedi",
                "analizza immagine",
                "foto",
                "descrivi l'immagine",
                "leggi il testo",
                # English
                "screenshot",
                "ocr",
                "what do you see",
                "analyze image",
                "photo",
                "describe the image",
                "read the text",
            ],
            priority=10,
            description="Image and visual content analysis",
        )

        # Multi-modal reasoning patterns (Vision + Reasoning combined)
        self.intent_classifier.register_intent(
            intent_name="multimodal_reasoning",
            patterns=[
                # Italian patterns (backward compatibility)
                "analizza questo diagramma",
                "spiega il flusso",
                "ragiona sull'immagine",
                "comprendi l'architettura",
                "analizza schema",
                "interpreta grafico",
                "reasoning visivo",
                "analisi tecnica immagine",
                # English
                "analyze this diagram",
                "explain the flow",
                "reason about the image",
                "understand the architecture",
                "analyze schema",
                "interpret graph",
                "visual reasoning",
                "technical image analysis",
            ],
            priority=15,  # Higher priority than individual vision/reasoning
            description="Combined vision analysis with multi-step reasoning for complex visual understanding",
        )

        # Collaborative/Swarm task patterns
        self.intent_classifier.register_intent(
            intent_name="collaborative_task",
            patterns=[
                # Italian patterns (backward compatibility)
                "collabora",
                "team di agenti",
                "baselith-coree",
                "ricerca approfondita",
                "analisi complessa",
                "molteplici prospettive",
                "progetto collaborativo",
                "compito complesso",
                # English
                "swarm",
                "collaborate",
                "agent team",
                "baselith-core",
                "deep research",
                "complex analysis",
                "multiple perspectives",
                "collaborative project",
                "complex task",
            ],
            priority=12,
            description="Complex tasks requiring baselith-core collaboration and parallel execution",
        )

    def classify_intent(self, query: str) -> str:
        """
        Classify user intent from query.

        Args:
            query: User input text

        Returns:
            Intent name
        """
        # Backward-compat sync wrapper.
        # WARNING: Must NOT be called from within a running event loop.
        try:
            asyncio.get_running_loop()
            raise RuntimeError(
                "classify_intent() cannot be called from an async context. "
                "Use 'await classify_intent_async()' instead."
            )
        except RuntimeError as exc:
            if "no running event loop" not in str(exc):
                raise
        return asyncio.run(self.classify_intent_async(query))

    async def classify_intent_async(self, query: str) -> str:
        """
        Classify user intent from query (async).

        Args:
            query: User input text

        Returns:
            Intent name
        """
        return await self.intent_classifier.classify(query)
