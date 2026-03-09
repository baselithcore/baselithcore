"""
Chain of Thought (CoT) Reasoning Module.

This module implements the explicit step-by-step reasoning pattern, allowing
agents to decompose complex queries into a structured 'reasoning trace'
before arriving at a final conclusion.
"""

import asyncio
import re
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class ReasoningStep:
    """
    Represents a single atomic unit of a reasoning chain.

    Attributes:
        step_number: The sequential index of the thought in the chain.
        thought: The descriptive text of the reasoning step.
        conclusion: Optional intermediate result or inference derived.
    """

    step_number: int
    thought: str
    conclusion: Optional[str] = None


class ChainOfThought:
    """
    Orchestrator for step-by-step cognitive processing.

    This class interacts with an LLM to generate a sequence of thoughts (trace)
    that logically leads to an answer. It handles parsing the raw LLM
    response into structured `ReasoningStep` objects.
    """

    COT_PROMPT = """Think through this step by step:

Question: {question}

Let's break this down:
1. First, I need to understand...
2. Then, I should consider...
3. Based on this...

Provide your reasoning in clear numbered steps, then give a final answer."""

    def __init__(self, llm_service=None):
        """
        Initialize the Chain of Thought orchestrator.

        Args:
            llm_service: Protocol for LLM operations.
        """
        self._llm_service = llm_service

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

    async def reason(
        self,
        question: str,
        context: Optional[str] = None,
    ) -> tuple[str, List[ReasoningStep]]:
        """
        Execute a multi-step logical reasoning trace.

        Iteratively decomposes a complex query into sequential steps,
        evaluating evidence and refining conclusions until a final
        answer is reached.

        Args:
            question: The complex problem statement to solve.
            context: Optional background knowledge or retrieved facts.

        Returns:
            tuple[str, List[ReasoningStep]]: Final answer and the complete audit trail.
        """
        if not self.llm_service:
            return self._simple_reason(question)

        prompt = self.COT_PROMPT.format(question=question)
        if context:
            prompt = f"Context: {context}\n\n{prompt}"

        try:
            # Run sync LLM service in a thread to avoid blocking event loop
            response = await asyncio.to_thread(
                self.llm_service.generate_response, prompt
            )
            steps, answer = self._parse_reasoning(response)
            return answer, steps
        except Exception:
            return self._simple_reason(question)

    def _simple_reason(self, question: str) -> tuple[str, List[ReasoningStep]]:
        """
        Generate a basic reasoning trace as a fallback.

        Used when the LLM service is unavailable or fails.

        Args:
            question: The problem to address.

        Returns:
            tuple[str, List[ReasoningStep]]: Static error response and audit steps.
        """
        steps = [
            ReasoningStep(1, f"Analyzing: {question}"),
            ReasoningStep(2, "Considering available information"),
            ReasoningStep(3, "Drawing conclusion", "Further analysis needed"),
        ]
        return "Unable to reason without LLM service", steps

    def _parse_reasoning(self, text: str) -> tuple[List[ReasoningStep], str]:
        """
        Extract structured reasoning steps and the final answer from raw text.

        Uses regex and structural analysis to identify numbered thought
        steps and transition keywords (e.g., 'Answer:', 'Conclusion:').

        Args:
            text: The unstructured output from the LLM.

        Returns:
            tuple[List[ReasoningStep], str]: List of thoughts and final conclusion.
        """
        steps = []
        lines = text.strip().split("\n")

        # Regex for step detection (supports "1.", "1)", "Step 1:", etc.)
        step_pattern = re.compile(r"^\s*(?:Step\s*)?(\d+)[.):]\s*(.*)", re.IGNORECASE)

        # Capture strictly the thought steps
        current_step_num = 0
        answer_lines = []
        parsing_steps = True

        for line in lines:
            line = line.strip()
            if not line:
                continue

            match = step_pattern.match(line)
            if match and parsing_steps:
                step_num_str, content = match.groups()
                # Ensure we are not picking up a numbered list in the answer
                # Logic: if step numbers are sequential, safe to assume it's a step
                try:
                    step_num = int(step_num_str)
                    if step_num == current_step_num + 1:
                        steps.append(ReasoningStep(step_num, content))
                        current_step_num = step_num
                        continue
                except ValueError:
                    pass

            # Check for transition to answer
            if "answer" in line.lower() or "conclusion" in line.lower():
                parsing_steps = False
                # If the line itself contains content (e.g. "Answer: 42"), keep it
                # If it's a header like "### Answer", skip or keep
                if ":" in line:
                    answer_lines.append(line.split(":", 1)[1].strip())
                elif len(line) > 15:  # heuristic: long line probably has content
                    answer_lines.append(line)
                continue

            if not parsing_steps:
                answer_lines.append(line)
            # If we are parsing steps but didn't match a step, it might be continuation of previous step
            elif steps:
                steps[-1].thought += " " + line

        answer = "\n".join(answer_lines).strip()

        # Fallback if no specific answer section found
        if not answer and not steps:
            # Whole text considered answer if no steps found
            answer = text
        elif not answer:
            # If steps found but no answer section, take the last part?
            # Or just return empty string and let caller handle?
            # Let's say if we have steps, maybe the last step holds conclusion
            if steps:
                # Check if last step has conclusion
                pass

        return steps, answer
