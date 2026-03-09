"""
Self-Correction Reasoning Module.

Implements an iterative feedback loop where an LLM evaluates its own
responses for factual errors, logical inconsistencies, or incompleteness,
applying repairs until a quality threshold or iteration limit is reached.
"""

from core.observability.logging import get_logger
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from core.config.reasoning import ReasoningConfig

logger = get_logger(__name__)


@dataclass
class CorrectionResult:
    """
    Summary of a self-correction cycle.

    Attributes:
        original: The raw response before any corrections.
        corrected: The final output after the correction loop.
        corrections_made: Number of successful repair iterations performed.
        is_valid: Whether the final response passed internal validation.
    """

    original: str
    corrected: str
    corrections_made: int
    is_valid: bool


class SelfCorrector:
    """
    Orchestrator for autonomous response refinement.

    Leverages the 'Self-Correction' pattern to improve agent output
    reliability. It systematically prompts for error detection and
    merges validated corrections into the final response.
    """

    CORRECTION_PROMPT = """Review this response for errors and correct them:

Original: {response}

Check for:
1. Factual accuracy
2. Logical consistency
3. Completeness

If corrections needed, provide the corrected version.
If no corrections needed, respond with "NO CORRECTIONS NEEDED".

Corrected response:"""

    def __init__(
        self,
        llm_service=None,
        max_corrections: Optional[int] = None,
        config: Optional["ReasoningConfig"] = None,
    ):
        """
        Initialize the self-corrector.

        Args:
            llm_service: Protocol for LLM operations.
            max_corrections: Maximum number of correction iterations.
            config: Optional reasoning configuration settings.
        """
        self._llm_service = llm_service
        self._config = config

        # Use explicit value, or config, or default
        if max_corrections is not None:
            self.max_corrections = max_corrections
        elif config is not None:
            self.max_corrections = config.self_correction_max_iterations
        else:
            self.max_corrections = 2  # Default fallback

    @property
    def config(self) -> Optional["ReasoningConfig"]:
        """Lazy load config."""
        if self._config is None:
            try:
                from core.config import get_reasoning_config

                self._config = get_reasoning_config()
            except ImportError:
                pass
        return self._config

    @property
    def llm_service(self):
        """Lazy load LLM service."""
        if self._llm_service is None:
            try:
                from core.services.llm import get_llm_service

                self._llm_service = get_llm_service()
            except ImportError:
                pass
        return self._llm_service

    async def correct(
        self,
        response: str,
        context: Optional[str] = None,
    ) -> CorrectionResult:
        """
        Apply self-correction to a response.

        Args:
            response: The response to correct
            context: Optional context for validation

        Returns:
            CorrectionResult with original and corrected response
        """
        if not self.llm_service:
            return CorrectionResult(
                original=response,
                corrected=response,
                corrections_made=0,
                is_valid=True,
            )

        current = response
        corrections = 0

        for _ in range(self.max_corrections):
            prompt = self.CORRECTION_PROMPT.format(response=current)
            if context:
                prompt = f"Context: {context}\n\n{prompt}"

            try:
                result = await self.llm_service.generate_response(prompt=prompt)

                if "NO CORRECTIONS NEEDED" in result.upper():
                    break

                if result.strip() and result != current:
                    current = result.strip()
                    corrections += 1
                else:
                    break

            except Exception as e:
                logger.warning(f"Self-correction failed: {e}")
                break

        return CorrectionResult(
            original=response,
            corrected=current,
            corrections_made=corrections,
            is_valid=True,
        )
