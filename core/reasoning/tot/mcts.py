"""
Monte Carlo Tree Search (MCTS) for Reasoning.

Implements the MCTS algorithm specifically tailored for symbolic
reasoning spaces. Orchestrates the four phases: Selection (via UCT),
Expansion, Simulation (via LLM Evaluation), and Backpropagation.
"""

from collections.abc import Callable
from typing import Any

from core.observability.logging import get_logger
from core.reasoning.mcts_common import backpropagate_moving_avg
from core.reasoning.mcts_common import uct_score as _uct_score

from .tree import ThoughtNode

logger = get_logger(__name__)


def uct_select(node: ThoughtNode) -> ThoughtNode:
    """
    Select a node for expansion using the Upper Confidence Bound (UCT) formula.

    Performs tree traversal until an unexpanded node or terminal state
    is reached, prioritizing high-value paths with statistical uncertainy.

    Args:
        node: The root node of the current subtree.

    Returns:
        ThoughtNode: The selected node for the next simulation phase.
    """
    while node.children:
        unvisited = [c for c in node.children if c.visits == 0]
        if unvisited:
            return unvisited[0]

        parent_visits = node.visits  # uct_score guards child_visits==0
        node = max(
            node.children,
            key=lambda c: _uct_score(c.value, c.visits, parent_visits, exploration=1.0),
        )

    return node


def backpropagate(node: ThoughtNode, value: float) -> None:
    """
    Update node statistics iteratively from the leaf up to the root.

    Args:
        node: The node where evaluation/simulation occurred.
        value: The reward or score to propagate upwards.
    """
    backpropagate_moving_avg(node, value)


def get_best_leaf(root: ThoughtNode) -> ThoughtNode | None:
    """Traverse down the best path to find the leaf.

    Args:
        root: Root of the tree.

    Returns:
        Best leaf node based on score.
    """
    curr = root
    while curr.children:
        # Pick best score child
        curr = max(curr.children, key=lambda x: x.score)
    return curr


def mcts_search(
    root: ThoughtNode,
    max_depth: int,
    generator: Callable[[ThoughtNode], list[ThoughtNode]],
    evaluator: Callable[[list[ThoughtNode]], list[float]],
    iterations: int = 30,
) -> ThoughtNode | None:
    """
    Executes a synchronous Monte Carlo Tree Search.

    Systematically traverses the reasoning space to find the most
    promising path by balancing discovery of new thoughts with deep
    refinement of existing ones.
    """
    best_node = root

    for i in range(iterations):
        # 1. Selection
        node = uct_select(root)

        # If we reached max depth or it's terminal, backpropagate current value
        if node.depth >= max_depth:
            backpropagate(node, node.score)
            continue

        # 2. Expansion
        if not node.children:
            children = generator(node)
            if not children:
                # Terminal node
                backpropagate(node, node.score)
                continue

            node.children = children

            # 3. Simulation (Evaluation)
            scores = evaluator(children)

            # Update children scores
            max_child_score = 0.0
            for child, score in zip(children, scores):
                child.parent = node
                child.score = score
                child.value = score
                child.visits = 1
                if score > max_child_score:
                    max_child_score = score

                # Track global best
                if score > best_node.score:
                    best_node = child

            # 4. Backpropagation
            backpropagate(node, max_child_score)

    return best_node


def _active_budget() -> Any | None:
    """The ambient request LoopBudget, or None outside an orchestrated run."""
    try:
        from core.orchestration.budget_context import get_active_budget

        return get_active_budget()
    except Exception:
        return None


#: Iterations without a better best node before the async search gives up.
#: Every iteration costs one generation call plus ``branching_factor``
#: evaluations, so a search that has stopped improving is burning LLM budget
#: for nothing. ``None`` disables the early stop.
DEFAULT_PATIENCE = 8


async def mcts_search_async(
    root: ThoughtNode,
    max_depth: int,
    generator: Callable,
    evaluator: Callable,
    iterations: int = 30,
    problem: str = "",
    branching_factor: int = 3,
    patience: int | None = DEFAULT_PATIENCE,
) -> ThoughtNode | None:
    """
    Perform Asynchronous Monte Carlo Tree Search.

    Phases:
    1. Selection: Select a leaf node using UCT (CPU bound).
    2. Expansion: Generate children async.
    3. Simulation: Evaluate children async.
    4. Backpropagation: Update stats (CPU bound).

    The loop is bounded three ways, not just by ``iterations``: the ambient
    :class:`~core.orchestration.limits.LoopBudget` deadline is checked every
    iteration (the orchestrator ticks it once for the whole flow, so nothing
    else consults it here), and the search stops early once ``patience``
    iterations pass without improving the best node. Without either, a full run
    is ``iterations x (1 + branching_factor)`` serialized LLM round trips —
    ~120 at the defaults — with only the USD charge able to stop it.

    Args:
        root: Root node of the search tree.
        max_depth: Maximum tree depth.
        generator: Async function to generate child nodes.
        evaluator: Async function to evaluate nodes.
        iterations: Number of MCTS iterations.
        problem: Problem description for generation/evaluation.
        branching_factor: Number of children to generate per expansion.
        patience: Iterations without improvement before stopping early;
            ``None`` runs the full iteration count.

    Returns:
        Best node found during search.
    """
    best_node = root
    budget = _active_budget()
    stalled = 0

    for _i in range(iterations):
        if budget is not None:
            # Raises BudgetExceededError past the wall-clock deadline; the
            # caller treats that as a terminated flow rather than a crash.
            budget.check_deadline()

        # 1. Selection (Sync - CPU bound)
        node = uct_select(root)

        # If we reached max depth, backpropagate current value
        if node.depth >= max_depth:
            backpropagate(node, node.score)
            continue

        # 2. Expansion (Async - IO bound)
        if not node.children:
            children = await generator(node, branching_factor, problem)

            if not children:
                # Terminal node
                backpropagate(node, node.score)
                continue

            node.children = children

            # 3. Simulation / Evaluation (Async - IO bound)
            scores = await evaluator(children, problem)

            max_child_score = 0.0
            improved = False
            for child, score in zip(children, scores):
                child.parent = node
                child.score = score
                child.value = score
                child.visits = 1
                if score > max_child_score:
                    max_child_score = score

                # Track global best
                if score > best_node.score:
                    best_node = child
                    improved = True

            # 4. Backpropagation (Sync - CPU bound)
            backpropagate(node, max_child_score)

            stalled = 0 if improved else stalled + 1
            if patience is not None and stalled >= patience:
                logger.debug(
                    "MCTS stopped early after %d iterations without improvement "
                    "(best score %.3f)",
                    stalled,
                    best_node.score,
                )
                break

    return best_node
