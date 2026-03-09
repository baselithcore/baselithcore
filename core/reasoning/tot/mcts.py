"""
Monte Carlo Tree Search (MCTS) for Reasoning.

Implements the MCTS algorithm specifically tailored for symbolic
reasoning spaces. Orchestrates the four phases: Selection (via UCT),
Expansion, Simulation (via LLM Evaluation), and Backpropagation.
"""

from core.observability.logging import get_logger
from typing import Callable, List, Optional

from core.reasoning.mcts_common import uct_score as _uct_score, backpropagate_moving_avg
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


def get_best_leaf(root: ThoughtNode) -> Optional[ThoughtNode]:
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
    generator: Callable[[ThoughtNode], List[ThoughtNode]],
    evaluator: Callable[[List[ThoughtNode]], List[float]],
    iterations: int = 30,
) -> Optional[ThoughtNode]:
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


async def mcts_search_async(
    root: ThoughtNode,
    max_depth: int,
    generator: Callable,
    evaluator: Callable,
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

    Args:
        root: Root node of the search tree.
        max_depth: Maximum tree depth.
        generator: Async function to generate child nodes.
        evaluator: Async function to evaluate nodes.
        iterations: Number of MCTS iterations.
        problem: Problem description for generation/evaluation.
        branching_factor: Number of children to generate per expansion.

    Returns:
        Best node found during search.
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
            children = await generator(node, branching_factor, problem)

            if not children:
                # Terminal node
                backpropagate(node, node.score)
                continue

            node.children = children

            # 3. Simulation / Evaluation (Async - IO bound)
            scores = await evaluator(children, problem)

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

            # 4. Backpropagation (Sync - CPU bound)
            backpropagate(node, max_child_score)

    return best_node
