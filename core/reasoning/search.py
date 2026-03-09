"""
Search Algorithms for Reasoning Trees.

Provides classic graph search strategies (BFS/Beam Search and DFS)
adapted for the Tree of Thoughts pattern. Enables controlled
exploration of the reasoning space.
"""

from core.observability.logging import get_logger
from typing import List, Callable, Optional
from .tot import ThoughtNode

logger = get_logger(__name__)


def breadth_first_search(
    root: ThoughtNode,
    max_depth: int,
    beam_width: int,
    generator: Callable[[ThoughtNode], List[ThoughtNode]],
    evaluator: Callable[[List[ThoughtNode]], List[float]],
) -> Optional[ThoughtNode]:
    """
    Breadth-First Search (BFS) with Beam Search strategy.

    Explores the reasoning tree level-by-level, keeping only the top
    'beam_width' thoughts at each depth to prevent exponential
    explosion of the search space.

    Args:
        root: The starting ThoughtNode.
        max_depth: Maximum levels to traverse.
        beam_width: Number of top nodes to keep per level.
        generator: Function that yields children for a node.
        evaluator: Function that scores a batch of nodes.

    Returns:
        Optional[ThoughtNode]: The highest-scoring leaf node found.
    """
    current_level = [root]

    for depth in range(max_depth):
        if not current_level:
            break

        logger.debug(f"BFS: Depth {depth}, processing {len(current_level)} nodes")

        candidates = []
        for node in current_level:
            # Generate children
            children = generator(node)
            if not children:
                continue

            # Link children
            for child in children:
                node.children.append(child)
                child.parent = node

            # Evaluate children (batch evaluation could be optimized here)
            scores = evaluator(children)
            for child, score in zip(children, scores):
                child.score = score
                candidates.append(child)

        # Select best candidates (Beam Search)
        # Sort by score descending
        candidates.sort(key=lambda x: x.score, reverse=True)
        current_level = candidates[:beam_width]

    # Return best node from the last level reached
    return current_level[0] if current_level else root


def depth_first_search(
    root: ThoughtNode,
    max_depth: int,
    generator: Callable[[ThoughtNode], List[ThoughtNode]],
    evaluator: Callable[[List[ThoughtNode]], List[float]],
    threshold: float = 0.5,
) -> Optional[ThoughtNode]:
    """
    Depth-First Search with iterative deepening or simple pruning.
    For simplicity, this acts as a greedy DFS with pruning.

    Args:
        root: Root node.
        max_depth: limit.
        generator: child generation function.
        evaluator: scoring function.
        threshold: minimum score to continue branching.

    Returns:
        Best node found.
    """
    stack: List[ThoughtNode] = [root]
    best_node = root

    while stack:
        node = stack.pop()

        if node.score >= best_node.score:
            best_node = node

        if node.depth >= max_depth:
            continue

        children = generator(node)
        if not children:
            continue

        # Link
        for child in children:
            child.parent = node

        # Evaluate
        scores = evaluator(children)

        # Filter and add to stack (higher scores processed last -> LIFO)
        # So we actually want to sort ascending so best ones are at the end (top) of stack
        scored_children = []
        for child, score in zip(children, scores):
            child.score = score
            if score >= threshold:
                scored_children.append(child)

        scored_children.sort(key=lambda x: x.score)

        for child in scored_children:
            stack.append(child)

    return best_node
