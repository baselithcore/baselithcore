"""
Output Refinement Logic for Reflection.

Implements the corrective phase of the reflection pattern. Transforms
suboptimal responses into high-quality outputs by incorporating targeted
critique into a subsequent LLM generation pass.
"""

from typing import Optional

from core.observability.logging import get_logger

logger = get_logger(__name__)

# Refinement prompt template
REFINEMENT_PROMPT = """Improve this AI response based on the feedback provided.

ORIGINAL USER QUERY:
{query}

CURRENT RESPONSE:
{response}

FEEDBACK FOR IMPROVEMENT:
{feedback}

Generate an improved version of the response that addresses the feedback.
Maintain the same format and style, but improve quality based on the feedback.
Only output the improved response, nothing else."""


class DefaultRefiner:
    """
    Standard LLM-powered corrective refiner.

    Translates evaluation feedback into concrete response improvements.
    Ensures the final output respects the original user intent while
    addressing identified deficiencies in accuracy or clarity.
    """

    def __init__(self, llm_service=None):
        """
        Initialize refiner.

        Args:
            llm_service: LLMService instance. If None, imports from core.services.llm
        """
        self._llm_service = llm_service

    @property
    def llm_service(self):
        """Lazy load LLM service."""
        if self._llm_service is None:
            from core.services.llm import get_llm_service

            self._llm_service = get_llm_service()
        return self._llm_service

    async def refine(
        self,
        response: str,
        feedback: str,
        query: str,
        context: Optional[dict] = None,
    ) -> str:
        """
        Refine a response based on feedback.

        Args:
            response: The response to refine
            feedback: Improvement feedback
            query: The original query
            context: Optional context (unused in default implementation)

        Returns:
            Refined response string
        """
        prompt = REFINEMENT_PROMPT.format(
            query=query,
            response=response,
            feedback=feedback,
        )

        try:
            refined = await self.llm_service.generate_response(prompt)

            # Validate refinement is not empty
            if not refined or len(refined.strip()) < 10:
                logger.warning("Refinement returned empty, using original")
                return response

            logger.info(f"Refined response: {len(response)} -> {len(refined)} chars")
            return refined

        except Exception as e:
            logger.warning(f"Error during refinement: {e}, returning original")
            return response
