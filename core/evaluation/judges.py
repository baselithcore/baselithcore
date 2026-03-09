"""
Standard Evaluator Implementations (LLM Judges).
"""

from typing import Dict, Optional

from .base import BaseLLMEvaluator


class RelevanceEvaluator(BaseLLMEvaluator):
    """Evaluates if the response directly addresses the user's query."""

    def get_prompt(
        self, query: str, response: str, context: Optional[Dict] = None
    ) -> str:
        """
        Construct the relevance evaluation prompt.

        Args:
            query: User's original query.
            response: AI's generated response.
            context: Additional context data.

        Returns:
            Formatted prompt string for the LLM judge.
        """
        return f"""You are a Relevance Judge. Evaluate how well the response answers the specific user query.

USER QUERY:
{query}

RESPONSE:
{response}

Score 0.0 to 1.0 (1.0 = perfect direct answer).
Explain reasoning in 'feedback'.

Return JSON:
{{
    "score": <float>,
    "feedback": "<string>",
    "should_refine": <boolean>
}}"""


class CoherenceEvaluator(BaseLLMEvaluator):
    """Evaluates clarity, logic, and structure."""

    def get_prompt(
        self, query: str, response: str, context: Optional[Dict] = None
    ) -> str:
        """
        Construct the coherence evaluation prompt.

        Args:
            query: User's original query.
            response: AI's generated response.
            context: Additional context data.

        Returns:
            Formatted prompt string for the LLM judge.
        """
        return f"""You are a Coherence Judge. Evaluate the clarity and logical flow of the response.

RESPONSE:
{response}

Score 0.0 to 1.0 (1.0 = perfectly clear and logical).
Explain reasoning in 'feedback'.

Return JSON:
{{
    "score": <float>,
    "feedback": "<string>",
    "should_refine": <boolean>
}}"""


class FaithfulnessEvaluator(BaseLLMEvaluator):
    """Evaluates if the response is grounded in the provided context (RAG check)."""

    def get_prompt(
        self, query: str, response: str, context: Optional[Dict] = None
    ) -> str:
        """
        Construct the faithfulness evaluation prompt.

        Args:
            query: User's original query.
            response: AI's generated response.
            context: Additional context data.

        Returns:
            Formatted prompt string for the LLM judge.
        """
        context_text = (
            context.get("memory_context", "No context provided.")
            if context
            else "No context provided."
        )

        return f"""You are a Faithfulness Judge. Verify if the response is supported by the context.
If no context is provided, assume neutrality but penalize hallucinations of specific facts.

CONTEXT:
{context_text}

RESPONSE:
{response}

Score 0.0 to 1.0 (1.0 = fully supported by context).
Explain any hallucinations in 'feedback'.

Return JSON:
{{
    "score": <float>,
    "feedback": "<string>",
    "should_refine": <boolean>
}}"""


class CompositeEvaluator(BaseLLMEvaluator):
    """
    Runs multiple evaluators and aggregates the result.
    This doesn't use an LLM directly but orchestrates others.
    """

    def __init__(self, evaluators=None):
        super().__init__()
        self.evaluators = evaluators or [
            RelevanceEvaluator(),
            CoherenceEvaluator(),
        ]

    async def evaluate(self, response: str, query: str, context: Optional[Dict] = None):
        """Run all evaluators and average the score."""
        from .protocols import EvaluationResult

        total_score = 0.0
        aspects = {}
        feedbacks = []
        should_refine = False

        for judge in self.evaluators:
            result = await judge.evaluate(response, query, context)
            name = judge.__class__.__name__.replace("Evaluator", "").lower()
            aspects[name] = result.score
            total_score += result.score
            feedbacks.append(f"{name}: {result.feedback}")
            if result.should_refine:
                should_refine = True

        avg_score = total_score / len(self.evaluators) if self.evaluators else 0.0

        return EvaluationResult(
            score=avg_score,
            quality=self._score_to_quality(avg_score),
            feedback=" | ".join(feedbacks),
            should_refine=should_refine,
            aspects=aspects,
            metadata={"type": "composite"},
        )

    def get_prompt(
        self, query: str, response: str, context: Optional[Dict] = None
    ) -> str:
        """
        Construct the relevance evaluation prompt.

        Returns:
            The formatted prompt string.
        """
        raise NotImplementedError("CompositeEvaluator does not use a single prompt.")
