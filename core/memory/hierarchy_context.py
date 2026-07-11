"""Context assembly for hierarchical memory.

``get_context`` renders the three tiers into a token-budgeted markdown block
for LLM injection. Split out of ``hierarchy.py`` to respect the module size
cap; mixed into :class:`~core.memory.hierarchy.HierarchicalMemory`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from .types import MemoryItem


class HierarchyContextMixin:
    """Token-budgeted context rendering over the STM/MTM/LTM tiers."""

    # Provided by HierarchicalMemory (annotations mirror HierarchySearchMixin).
    _stm: list[MemoryItem]
    _mtm: list[MemoryItem]
    _ltm: Iterable[MemoryItem]  # deque(maxlen=...) in HierarchicalMemory

    def get_context(self, max_tokens: int = 2000) -> str:
        """
        Assemble a formatted context string for LLM injection.

        Retrieves and ranks information across all tiers, prioritizing
        immediate STM sequence, following by recent MTM clusters and
        historical LTM summaries.

        Budgets by **tokens** (via ``core.utils.tokens.estimate_tokens``:
        tiktoken when available, a content-aware heuristic otherwise) — not raw
        character length — so the returned context actually fits the model
        window the caller sized ``max_tokens`` against.

        Args:
            max_tokens: Token budget for the total returned context.

        Returns:
            str: A formatted markdown block containing structured
                 context sections.
        """
        from core.utils.tokens import estimate_tokens

        parts = []
        remaining = max_tokens
        # ~20 tokens of headroom before opening a new section (heading + a line).
        _SECTION_MIN = 20

        # STM gets priority
        if self._stm:
            parts.append("## Recent Context")
            for item in reversed(self._stm):  # Most recent first
                line = f"- {item.content}\n"
                cost = estimate_tokens(line)
                if cost > remaining:
                    break
                parts.append(line)
                remaining -= cost

        # MTM next
        if self._mtm and remaining > _SECTION_MIN:
            parts.append("\n## Background")
            for item in reversed(self._mtm[-5:]):  # Last 5
                line = f"- {item.content}\n"
                cost = estimate_tokens(line)
                if cost > remaining:
                    break
                parts.append(line)
                remaining -= cost

        # LTM summaries if space
        if self._ltm and remaining > _SECTION_MIN:
            summaries = [i for i in self._ltm if i.metadata.get("is_summary")]
            if summaries:
                parts.append("\n## Long-term Knowledge")
                for item in summaries[-3:]:
                    line = f"- {item.content}\n"
                    cost = estimate_tokens(line)
                    if cost > remaining:
                        break
                    parts.append(line)
                    remaining -= cost

        return "".join(parts)


__all__ = ["HierarchyContextMixin"]
