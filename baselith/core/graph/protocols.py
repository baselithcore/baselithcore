"""
Structural Types for Decoded Graph Payloads.

The FalkorDB/RedisGraph client ships no type information, so every value it
hands back arrives as ``Any``. These protocols name the two shapes this
package actually relies on, so the places that assume the client's contract
are explicit, reviewable ``cast`` sites instead of silent ``Any`` leaks.
"""

from __future__ import annotations

from typing import Any, Protocol


class GraphEntity(Protocol):
    """A decoded node or edge: anything carrying a property mapping.

    Property *values* stay ``Any`` on purpose: the graph stores arbitrary
    scalars and the client does not narrow them.
    """

    @property
    def properties(self) -> dict[str, Any]:
        """Properties of the entity, keyed by property name."""
        ...


class GraphNode(GraphEntity, Protocol):
    """A decoded node: a :class:`GraphEntity` that also carries its labels."""

    @property
    def labels(self) -> list[str]:
        """Labels attached to the node."""
        ...


__all__ = ["GraphEntity", "GraphNode"]
