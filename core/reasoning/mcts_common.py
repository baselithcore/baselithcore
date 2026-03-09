"""
Shared MCTS Utilities.

Provides the mathematical primitives for Monte Carlo Tree Search (MCTS)
implementations across the framework. Includes UCB1 scoring and
multi-strategy backpropagation (moving average vs cumulative).
"""

import math


def uct_score(
    child_value: float,
    child_visits: int,
    parent_visits: int,
    exploration: float = 1.41,
) -> float:
    """
    Computes the Upper Confidence Bound (UCB1) score for a tree node.

    Balances exploitation (average value) and exploration (uncertainty
    bonus based on relative visit counts).

    Args:
        child_value: The estimated value (Q-value) of the child node.
        child_visits: Number of times this child has been explored.
        parent_visits: Total visits to the parent node.
        exploration: Constant controlling exploration vs exploitation trade-off.

    Returns:
        The computed UCB1 score. Unvisited nodes return infinity to
        prioritize initial discovery.
    """
    if child_visits == 0:
        return float("inf")
    exploitation = child_value
    exploration_term = exploration * math.sqrt(
        math.log(max(parent_visits, 1)) / child_visits
    )
    return exploitation + exploration_term


def backpropagate_moving_avg(
    node,
    value: float,
    *,
    parent_attr: str = "parent",
    visits_attr: str = "visits",
    value_attr: str = "value",
) -> None:
    """Backpropagate a value up the tree using moving average.

    Each ancestor's value is updated via the incremental mean formula:
    ``Q_{n+1} = Q_n + (v - Q_n) / (n + 1)``.

    This variant is used by the Tree-of-Thought MCTS.

    Args:
        node: Starting node (will be updated and then its parent, etc.).
        value: Value to propagate.
        parent_attr: Name of the attribute pointing to the parent node.
        visits_attr: Name of the visit-count attribute.
        value_attr: Name of the value attribute (updated as moving average).
    """
    curr = node
    while curr is not None:
        visits = getattr(curr, visits_attr) + 1
        setattr(curr, visits_attr, visits)
        old_value = getattr(curr, value_attr)
        setattr(curr, value_attr, old_value + (value - old_value) / visits)
        curr = getattr(curr, parent_attr, None)


def backpropagate_cumulative(
    node,
    reward: float,
    *,
    parent_attr: str = "parent",
    visits_attr: str = "visits",
    reward_attr: str = "total_reward",
) -> None:
    """Backpropagate a reward up the tree using cumulative sum.

    The average is computed on read as ``total_reward / visits``.

    This variant is used by the World Model MCTS.

    Args:
        node: Starting node.
        reward: Reward to accumulate.
        parent_attr: Name of the attribute pointing to the parent node.
        visits_attr: Name of the visit-count attribute.
        reward_attr: Name of the cumulative reward attribute.
    """
    curr = node
    while curr is not None:
        setattr(curr, visits_attr, getattr(curr, visits_attr) + 1)
        setattr(curr, reward_attr, getattr(curr, reward_attr) + reward)
        curr = getattr(curr, parent_attr, None)
