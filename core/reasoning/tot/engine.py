"""
Tree of Thoughts (ToT) Execution Engine.

The central orchester for the Tree of Thoughts pattern. Coordinates
thought generation, evaluation, and search strategies to solve
complex problems that require non-linear exploration.
"""

import asyncio
from core.observability.logging import get_logger
import re
from typing import List, Optional

from core.reasoning.prompts import (
    THOUGHT_EVALUATION_PROMPT,
    THOUGHT_GENERATION_PROMPT,
)

from .mcts import backpropagate, get_best_leaf, mcts_search, uct_select
from .tree import ThoughtNode, export_tree_to_mermaid

logger = get_logger(__name__)


class TreeOfThoughts:
    """
    Controller for the Tree of Thoughts (ToT) reasoning loop.

    Implements both BFS (Beam Search) and MCTS strategies. Supports
    tool-augmented reasoning where thoughts can be validated by
    executing code in a sandbox.
    """

    def __init__(self, llm_service=None):
        """
        Initialize the Tree of Thoughts engine.

        Args:
            llm_service: Protocol for LLM operations.
        """
        self._llm_service = llm_service
        self.tools: List = []

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

    def _generate_thoughts(
        self, node: ThoughtNode, k: int, problem: str
    ) -> List[ThoughtNode]:
        """
        Generate k next possible thoughts from the current state.

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
            response = self.llm_service.generate_response(prompt)
            thoughts = []
            for line in response.strip().split("\n"):
                line = line.strip()
                if line and (line[0].isdigit() and (line[1] == "." or line[1] == ")")):
                    parts = line.split(" ", 1)
                    if len(parts) > 1:
                        thoughts.append(parts[1].strip())

            if not thoughts:
                thoughts = [response.strip()]

            return [ThoughtNode(content=t, depth=node.depth + 1) for t in thoughts[:k]]

        except Exception as e:
            logger.error(f"Error generating thoughts: {e}")
            return []

    def _evaluate_thoughts(self, nodes: List[ThoughtNode], problem: str) -> List[float]:
        """
        Evaluate a batch of thoughts for quality and progress.

        Calculates scores (0.0-1.0) using the LLM evaluator for each node.

        Args:
            nodes: Candidates for evaluation.
            problem: Context for evaluation criteria.

        Returns:
            List[float]: Ranked scores matching the input nodes.
        """
        scores = []
        for node in nodes:
            prompt = THOUGHT_EVALUATION_PROMPT.format(
                problem=problem, thought=node.content
            )
            try:
                response = self.llm_service.generate_response(prompt)
                match = re.search(r"0\.\d+|1\.0|0|1", response)
                if match:
                    scores.append(float(match.group()))
                else:
                    scores.append(0.5)
            except Exception as e:
                logger.error(f"Error evaluating thought: {e}")
                scores.append(0.0)
        return scores

    async def _evaluate_thought_single_async(
        self, node: ThoughtNode, problem: str
    ) -> float:
        """Evaluate a single thought asynchronously."""
        loop = asyncio.get_running_loop()

        def _eval_sync():
            """
            Synchronous evaluation wrapper for thread pool execution.

            Returns:
                The extracted evaluation score.
            """
            scores = self._evaluate_thoughts([node], problem)
            return scores[0] if scores else 0.0

        return await loop.run_in_executor(None, _eval_sync)

    async def solve(
        self,
        problem: str,
        k: int = 3,
        max_steps: int = 5,
        tools: Optional[List] = None,
        strategy: str = "mcts",
        initial_state: Optional[str] = None,
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

            # Check if this instance is the Async version
            if hasattr(self, "_mcts_search_async"):
                best_leaf = await self._mcts_search_async(
                    root,
                    max_depth=max_steps,
                    iterations=iterations,
                    problem=problem,
                    branching_factor=branching_factor,
                )
            else:
                # Use sync MCTS
                best_leaf = mcts_search(
                    root,
                    max_depth=max_steps,
                    generator=lambda n: self._generate_thoughts(n, k, problem),
                    evaluator=lambda nodes: self._evaluate_thoughts(nodes, problem),
                    iterations=iterations,
                )

        if not best_leaf:
            # Default fallback loop
            for i in range(10):
                node = uct_select(root)
                new_nodes = await self._expand(node, k, problem)
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
    ) -> List[ThoughtNode]:
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
            response = await self.llm_service.generate_response_async(prompt)
            thoughts = []

            lines = response.strip().split("\n")
            for line in lines:
                cleaned = re.sub(r"^\d+[\.)]\\s*", "", line).strip()
                if cleaned:
                    thoughts.append(cleaned)

            if not thoughts and response.strip():
                thoughts = [response.strip()]

            # Tool Execution Logic
            processed_thoughts = []
            for thought_content in thoughts[:k]:
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
    Async version of Tree of Thoughts with parallelized operations.

    Uses asyncio.gather() to parallelize thought generation and evaluation,
    significantly reducing solve time for complex problems.

    Example:
        ```python
        tot = TreeOfThoughtsAsync(llm_service=my_async_llm)
        result = await tot.solve("How to optimize database?")
        ```
    """

    async def _generate_thought_single_async(
        self, node: ThoughtNode, problem: str
    ) -> Optional[ThoughtNode]:
        """
        Generate a single next thought asynchronously.

        Args:
            node: Parent state node.
            problem: The goal context.

        Returns:
            Optional[ThoughtNode]: A new thought node or None if generation failed.
        """
        state = "\n".join(node.get_path())
        prompt = THOUGHT_GENERATION_PROMPT.format(problem=problem, state=state, k=1)

        try:
            if hasattr(self.llm_service, "generate_response_async"):
                response = await self.llm_service.generate_response_async(prompt)
            else:
                loop = asyncio.get_running_loop()
                response = await loop.run_in_executor(
                    None, self.llm_service.generate_response, prompt
                )

            for line in response.strip().split("\n"):
                line = line.strip()
                if line and len(line) > 1:
                    if line[0].isdigit() and line[1] in ".)":
                        parts = line.split(" ", 1)
                        if len(parts) > 1:
                            return ThoughtNode(
                                content=parts[1].strip(), depth=node.depth + 1
                            )
            return ThoughtNode(content=response.strip(), depth=node.depth + 1)

        except Exception as e:
            logger.error(f"Error generating thought async: {e}")
            return None

    async def _generate_thoughts_async(
        self, node: ThoughtNode, k: int, problem: str
    ) -> List[ThoughtNode]:
        """Generate k thoughts in parallel."""
        tasks = [self._generate_thought_single_async(node, problem) for _ in range(k)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        thoughts = []
        for result in results:
            if isinstance(result, ThoughtNode):
                thoughts.append(result)
            elif isinstance(result, Exception):
                logger.warning(f"Thought generation failed: {result}")

        return thoughts

    async def _evaluate_thought_single_async(
        self, node: ThoughtNode, problem: str
    ) -> float:
        """Evaluate a single thought asynchronously."""
        prompt = THOUGHT_EVALUATION_PROMPT.format(problem=problem, thought=node.content)

        try:
            if hasattr(self.llm_service, "generate_response_async"):
                response = await self.llm_service.generate_response_async(prompt)
            else:
                loop = asyncio.get_running_loop()
                response = await loop.run_in_executor(
                    None, self.llm_service.generate_response, prompt
                )

            match = re.search(r"0\.\d+|1\.0|0|1", response)
            if match:
                return float(match.group())
            return 0.5

        except Exception as e:
            logger.error(f"Error evaluating thought async: {e}")
            return 0.0

    async def _evaluate_thoughts_async(
        self, nodes: List[ThoughtNode], problem: str
    ) -> List[float]:
        """Evaluate multiple thoughts in parallel."""
        tasks = [self._evaluate_thought_single_async(node, problem) for node in nodes]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        scores = []
        for result in results:
            if isinstance(result, float):
                scores.append(result)
            elif isinstance(result, Exception):
                logger.warning(f"Thought evaluation failed: {result}")
                scores.append(0.0)
            else:
                scores.append(0.0)

        return scores

    async def _mcts_search_async(
        self,
        root: ThoughtNode,
        max_depth: int,
        iterations: int = 30,
        problem: str = "",
        branching_factor: int = 3,
    ) -> Optional[ThoughtNode]:
        """
        Perform Asynchronous Monte Carlo Tree Search.

        Phases:
        1. Selection: Select a leaf node using UCT (CPU bound).
        2. Expansion: Generate children async.
        3. Simulation: Evaluate children async.
        4. Backpropagation: Update stats (CPU bound).
        """
        best_node = root

        for i in range(iterations):
            # 1. Selection (Sync - CPU bound)
            node = uct_select(root)

            # If we reached max depth, backpropagate current value
            if node.depth >= max_depth:
                backpropagate(node, node.score)
                continue

            # 2. Expansion (Async - IO bound)
            if not node.children:
                children = await self._generate_thoughts_async(
                    node, branching_factor, problem
                )

                if not children:
                    backpropagate(node, node.score)
                    continue

                node.children = children

                # 3. Simulation / Evaluation (Async - IO bound)
                scores = await self._evaluate_thoughts_async(children, problem)

                max_child_score = 0.0
                for child, score in zip(children, scores):
                    child.parent = node
                    child.score = score
                    child.value = score
                    child.visits = 1
                    if score > max_child_score:
                        max_child_score = score

                    if score > best_node.score:
                        best_node = child

                # 4. Backpropagation (Sync - CPU bound)
                backpropagate(node, max_child_score)

        return best_node
