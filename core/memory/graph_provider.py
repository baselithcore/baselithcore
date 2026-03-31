"""
Graph-based Memory Provider.

Provides a lightweight, in-memory adjacency list implementation
for tracking semantic relationships between entities (nodes).
"""

from typing import Dict, List, Optional
from core.observability.logging import get_logger
from .interfaces import GraphMemoryProvider

logger = get_logger(__name__)


class SimpleGraphMemoryProvider(GraphMemoryProvider):
    """
    Lightweight Graph Memory store.

    Uses a bidirectional adjacency list to track relationships.
    Suitable for small to medium-scale entity relationship tracking.
    """

    def __init__(self):
        # node_id -> List of {target, relation, weight}
        self._graph: Dict[str, List[dict]] = {}

    async def add_relation(
        self, source: str, relation: str, target: str, weight: float = 1.0
    ) -> None:
        """
        Add a directed relationship between source and target.
        """
        if source not in self._graph:
            self._graph[source] = []

        # Check for existing relation to update weight
        existing = next(
            (
                r
                for r in self._graph[source]
                if r["target"] == target and r["relation"] == relation
            ),
            None,
        )

        if existing:
            existing["weight"] = weight
        else:
            self._graph[source].append(
                {"target": target, "relation": relation, "weight": weight}
            )

        logger.debug(f"Graph relation added: {source} --[{relation}]--> {target}")

    async def get_neighbors(
        self, node: str, relation: Optional[str] = None
    ) -> List[dict]:
        """
        Get all entities directly connected to the specified node.
        """
        if node not in self._graph:
            return []

        results = self._graph[node]
        if relation:
            results = [r for r in results if r["relation"] == relation]

        return results

    async def query_graph(self, query: str, limit: int = 10) -> List[dict]:
        """
        Perform a simple traversal or keyword-based expansion.
        Currently implements a simple 1-hop expansion for the query entity.
        """
        # Basic heuristic: find if the query contains any known node names
        results = []
        for node in self._graph.keys():
            if node.lower() in query.lower():
                neighbors = await self.get_neighbors(node)
                for n in neighbors:
                    results.append(
                        {
                            "source": node,
                            "relation": n["relation"],
                            "target": n["target"],
                            "weight": n["weight"],
                        }
                    )

        return results[:limit]
