"""Memory-context injection: query-aware assembly + budget allocation record."""

from typing import Any

from core.orchestration.limits import LoopBudget
from core.orchestration.mixins._context_assembly import inject_memory_context


class _Orchestrator:
    def __init__(self, memory_manager: Any) -> None:
        self.memory_manager = memory_manager


class _QueryAwareMemory:
    """Stands in for HierarchicalMemory: sync get_context accepting a query."""

    def __init__(self) -> None:
        self.context_calls: list[dict[str, Any]] = []

    async def recall(self, query: str, limit: int = 5) -> list[Any]:
        return []

    def get_context(self, max_tokens: int = 2000, query: str | None = None) -> str:
        self.context_calls.append({"max_tokens": max_tokens, "query": query})
        return "## Recent Context\n- something remembered\n"


class _LegacyMemory:
    """Stands in for a manager whose get_context predates the query kwarg."""

    def __init__(self) -> None:
        self.context_calls: list[dict[str, Any]] = []

    async def recall(self, query: str, limit: int = 5) -> list[Any]:
        return []

    def get_context(self, max_tokens: int = 2000) -> str:
        self.context_calls.append({"max_tokens": max_tokens})
        return "## Recent Context\n- something remembered\n"


async def test_query_is_passed_to_a_query_aware_manager():
    memory = _QueryAwareMemory()
    context: dict[str, Any] = {}

    await inject_memory_context(
        _Orchestrator(memory), "kubernetes rollout", context, LoopBudget()
    )

    assert memory.context_calls[0]["query"] == "kubernetes rollout"


async def test_legacy_manager_without_query_kwarg_still_works():
    memory = _LegacyMemory()
    context: dict[str, Any] = {}

    await inject_memory_context(
        _Orchestrator(memory), "kubernetes rollout", context, LoopBudget()
    )

    assert memory.context_calls == [{"max_tokens": 2000}]
    assert context["recent_history"].startswith("## Recent Context")


async def test_injected_context_size_is_recorded_on_the_budget():
    budget = LoopBudget()

    await inject_memory_context(_Orchestrator(_QueryAwareMemory()), "query", {}, budget)

    # The assembled block is non-empty, so its token cost must be recorded.
    assert budget.context_tokens > 0


async def test_no_memory_manager_records_nothing():
    budget = LoopBudget()

    await inject_memory_context(_Orchestrator(None), "query", {}, budget)

    assert budget.context_tokens == 0
