"""
Tree of Thoughts (ToT) Execution Engine.

The central orchester for the Tree of Thoughts pattern. Coordinates
thought generation, evaluation, and search strategies to solve
complex problems that require non-linear exploration.
"""

import asyncio
import re

from core.observability.logging import get_logger
from core.reasoning.prompts import (
    THOUGHT_EVALUATION_PROMPT,
    THOUGHT_GENERATION_PROMPT,
)

from .mcts import backpropagate, get_best_leaf, mcts_search_async, uct_select
from .tree import ThoughtNode, export_tree_to_mermaid

logger = get_logger(__name__)

_SCORE_PATTERN = re.compile(r"0\.\d+|1\.0|0|1")
_NUMBERED_LINE_PATTERN = re.compile(r"^\d+[\.)]\s*")


class TreeOfThoughts:
    """
    Controller for the Tree of Thoughts (ToT) reasoning loop.

    Implements MCTS search over LLM-generated thoughts. Supports
    tool-augmented reasoning where thoughts can be validated by
    executing code in a sandbox.

    All LLM interactions go through the async ``LLMService.generate_response``
    API; a single generation call asks for the full branching factor of
    thoughts so sibling thoughts stay diverse (k identical prompts would be
    coalesced into one upstream call by the service's single-flight layer).
    """

    def __init__(self, llm_service=None):
        """
        Initialize the Tree of Thoughts engine.

        Args:
            llm_service: Protocol for LLM operations.
        """
        self._llm_service = llm_service
        self.tools: list = []

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

    @staticmethod
    def _parse_thoughts(response: str, k: int) -> list[str]:
        """
        Extract up to k thought strings from a numbered-list LLM response.

        Args:
            response: Raw LLM output expected to contain numbered lines.
            k: Maximum number of thoughts to keep.

        Returns:
            List[str]: Parsed thoughts; the whole response as a single
            thought if no numbered lines were found.
        """
        thoughts = []
        for line in response.strip().split("\n"):
            line = line.strip()
            cleaned = _NUMBERED_LINE_PATTERN.sub("", line)
            if cleaned and cleaned != line:
                thoughts.append(cleaned)

        if not thoughts and response.strip():
            thoughts = [response.strip()]

        return thoughts[:k]

    @staticmethod
    def _parse_score(response: str) -> float:
        """
        Extract a 0.0-1.0 score from an evaluation response.

        Args:
            response: Raw LLM output.

        Returns:
            float: Parsed score, 0.5 when no score is present.
        """
        match = _SCORE_PATTERN.search(response)
        return float(match.group()) if match else 0.5

    async def _generate_thoughts(
        self, node: ThoughtNode, k: int, problem: str
    ) -> list[ThoughtNode]:
        """
        Generate k next possible thoughts from the current state.

        Issues ONE generation call asking for k candidates rather than k
        identical single-thought calls.

        Args:
            node: The current state node in the tree.
            k: Target number of thoughts to generate.
            problem: Original problem description.

        Returns:
            List[ThoughtNode]: Generated candidate thoughts.
        """
        state = "\n".join(node.get_path())
        prompt = THOUGHT_GENERATION_PROMPT.format(problem=problem, state=state, k=k)

        try:
            response = await self.llm_service.generate_response(prompt)
            return [
                ThoughtNode(content=t, depth=node.depth + 1)
                for t in self._parse_thoughts(response, k)
            ]
        except Exception as e:
            logger.error(f"Error generating thoughts: {e}")
            return []

    # Kept under its historical name: mcts_search_async and the reasoning
    # handlers call the engine through this generator signature.
    async def _generate_thoughts_async(
        self, node: ThoughtNode, k: int, problem: str
    ) -> list[ThoughtNode]:
        """Generate k thoughts (single batched LLM call)."""
        return await self._generate_thoughts(node, k, problem)

    async def _generate_thought_single_async(
        self, node: ThoughtNode, problem: str
    ) -> ThoughtNode | None:
        """
        Generate a single next thought asynchronously.

        Args:
            node: Parent state node.
            problem: The goal context.

        Returns:
            Optional[ThoughtNode]: A new thought node or None if generation failed.
        """
        thoughts = await self._generate_thoughts(node, 1, problem)
        return thoughts[0] if thoughts else None

    async def _evaluate_thought_single_async(
        self, node: ThoughtNode, problem: str
    ) -> float:
        """Evaluate a single thought asynchronously.

        Routes the evaluation through the shared ThoughtCache so that
        structurally-identical thoughts (same content + problem context)
        reuse a previously computed score instead of re-hitting the LLM.
        Falls back to a direct evaluation if the cache is unavailable.
        """

        async def _eval(thought: str) -> float:
            prompt = THOUGHT_EVALUATION_PROMPT.format(problem=problem, thought=thought)
            try:
                response = await self.llm_service.generate_response(prompt)
                return self._parse_score(response)
            except Exception as e:
                logger.error(f"Error evaluating thought async: {e}")
                return 0.0

        try:
            from .cache import get_thought_cache

            cache = get_thought_cache()
        except Exception:
            cache = None

        if cache is None:
            return await _eval(node.content)

        return await cache.get_or_evaluate_async(node.content, problem, _eval)

    async def _evaluate_thoughts(
        self, nodes: list[ThoughtNode], problem: str
    ) -> list[float]:
        """
        Evaluate a batch of thoughts for quality and progress.

        Each evaluation is an independent LLM call, so the batch is fanned
        out concurrently. Failed evaluations score 0.0.

        Args:
            nodes: Candidates for evaluation.
            problem: Context for evaluation criteria.

        Returns:
            List[float]: Ranked scores matching the input nodes.
        """
        results = await asyncio.gather(
            *(self._evaluate_thought_single_async(node, problem) for node in nodes),
            return_exceptions=True,
        )

        scores = []
        for result in results:
            if isinstance(result, float):
                scores.append(result)
            else:
                if isinstance(result, BaseException):
                    logger.warning(f"Thought evaluation failed: {result}")
                scores.append(0.0)
        return scores

    async def _evaluate_thoughts_async(
        self, nodes: list[ThoughtNode], problem: str
    ) -> list[float]:
        """Evaluate multiple thoughts in parallel."""
        return await self._evaluate_thoughts(nodes, problem)

    async def _mcts_search_async(
        self,
        root: ThoughtNode,
        max_depth: int,
        iterations: int = 30,
        problem: str = "",
        branching_factor: int = 3,
    ) -> ThoughtNode | None:
        """
        Perform Asynchronous Monte Carlo Tree Search.

        Phases:
        1. Selection: Select a leaf node using UCT (CPU bound).
        2. Expansion: Generate children async.
        3. Simulation: Evaluate children async.
        4. Backpropagation: Update stats (CPU bound).
        """
        return await mcts_search_async(
            root,
            max_depth=max_depth,
            generator=self._generate_thoughts_async,
            evaluator=self._evaluate_thoughts_async,
            iterations=iterations,
            problem=problem,
            branching_factor=branching_factor,
        )

    async def solve(
        self,
        problem: str,
        k: int = 3,
        max_steps: int = 5,
        tools: list | None = None,
        strategy: str = "mcts",
        initial_state: str | None = None,
        **kwargs,
    ) -> dict:
        """
        Solve a problem using Tree of Thoughts with optional tools.

        Args:
            problem: Problem description to solve.
            k: Branching factor (number of thoughts per expansion).
            max_steps: Maximum tree depth.
            tools: Optional list of tools for thought execution.
            strategy: Search strategy ('mcts' or 'bfs').
            initial_state: Optional initial state for the root node.
            **kwargs: Additional arguments (iterations for MCTS).

        Returns:
            Dictionary with solution, steps, and tree visualization.
        """
        self.tools = tools or []
        root = ThoughtNode(content=initial_state or problem, score=0.0)
        best_leaf = None

        if strategy == "mcts":
            iterations = kwargs.get("iterations", 30)
            branching_factor = kwargs.get("branching_factor", k)

            best_leaf = await self._mcts_search_async(
                root,
                max_depth=max_steps,
                iterations=iterations,
                problem=problem,
                branching_factor=branching_factor,
            )

        if not best_leaf:
            # Default fallback loop — bounded by the configured step/iteration
            # budget so it can never spin unconditionally (regression guard).
            fallback_iterations = max(1, int(kwargs.get("iterations", max_steps)))
            for _ in range(fallback_iterations):
                node = uct_select(root)
                new_nodes = await self._expand(node, k, problem)
                if not new_nodes:
                    # No further expansion possible; stop early instead of
                    # burning the remaining iteration budget on empty work.
                    break
                for n in new_nodes:
                    score = await self._evaluate_thought_single_async(n, problem)
                    backpropagate(n, score)

            best_leaf = get_best_leaf(root)

        steps = best_leaf.get_path() if best_leaf else []

        return {
            "solution": best_leaf.content if best_leaf else "No solution found",
            "best_solution": best_leaf.content if best_leaf else "No solution found",
            "steps": steps,
            "tree_visualization": export_tree_to_mermaid(root),
            "tree_data": root.to_dict(),
        }

    async def _expand(
        self, node: ThoughtNode, k: int, problem: str
    ) -> list[ThoughtNode]:
        """
        Expand a node with both reasoned thoughts and optional tool outcomes.

        Coordinates parallel generation and evaluation of candidate paths.

        Args:
            node: The parent node to expand.
            k: Branching factor.
            problem: The task description.

        Returns:
            List[ThoughtNode]: Enriched and evaluated child nodes.
        """
        state = "\n".join(node.get_path())

        tool_prompt = ""
        if self.tools:
            tool_prompt = "\nYou can execute code to verify your thoughts. Use [EXECUTE]code[/EXECUTE] tag."

        prompt = (
            THOUGHT_GENERATION_PROMPT.format(problem=problem, state=state, k=k)
            + tool_prompt
        )

        try:
            response = await self.llm_service.generate_response(prompt)
            thoughts = self._parse_thoughts(response, k)

            # Tool Execution Logic
            processed_thoughts = []
            for thought_content in thoughts:
                code_match = re.search(
                    r"\[EXECUTE\](.*?)\[/EXECUTE\]", thought_content, re.DOTALL
                )
                if code_match and self.tools:
                    code = code_match.group(1).strip()
                    tool_output = "Tool execution failed"
                    try:
                        sandbox = self.tools[0]
                        result = await sandbox.execute_code_async(code)
                        tool_output = (
                            result.stdout if result.exit_code == 0 else result.stderr
                        )
                    except Exception as e:
                        tool_output = f"Error: {e}"

                    thought_content += f"\n[RESULT]\n{tool_output}\n[/RESULT]"

                processed_thoughts.append(
                    ThoughtNode(
                        content=thought_content, depth=node.depth + 1, parent=node
                    )
                )

            node.children.extend(processed_thoughts)
            return processed_thoughts

        except Exception as e:
            logger.error(f"Error generating thoughts: {e}")
            return []


class TreeOfThoughtsAsync(TreeOfThoughts):
    """
    Async Tree of Thoughts engine.

    Kept as a distinct name for backward compatibility: the base
    ``TreeOfThoughts`` is now fully async (generation batched per expansion,
    evaluations fanned out with ``asyncio.gather``), so this subclass adds
    no behavior.

    Example:
        ```python
        tot = TreeOfThoughtsAsync(llm_service=my_async_llm)
        result = await tot.solve("How to optimize database?")
        ```
    """
