"""
Tree of Thoughts (ToT) Module.

Provides Tree of Thoughts reasoning pattern with MCTS support
for complex problem solving.

Usage:
    from core.reasoning.tot import TreeOfThoughts, TreeOfThoughtsAsync, ThoughtNode

    # Sync usage
    tot = TreeOfThoughts()
    result = await tot.solve("Complex problem")

    # Async with parallel LLM calls
    tot_async = TreeOfThoughtsAsync()
    result = await tot_async.solve("Complex problem", strategy="mcts")
"""

from .engine import TreeOfThoughts, TreeOfThoughtsAsync
from .mcts import (
    backpropagate,
    get_best_leaf,
    mcts_search,
    mcts_search_async,
    uct_select,
)
from .tree import ThoughtNode, export_tree_to_json, export_tree_to_mermaid
from .cache import ThoughtCache, get_thought_cache

__all__ = [
    # Main classes
    "TreeOfThoughts",
    "TreeOfThoughtsAsync",
    # Tree structures
    "ThoughtNode",
    "export_tree_to_json",
    "export_tree_to_mermaid",
    # MCTS utilities
    "uct_select",
    "backpropagate",
    "get_best_leaf",
    "mcts_search",
    "mcts_search_async",
    # Caching
    "ThoughtCache",
    "get_thought_cache",
]
