"""Parallel crews must bound their fan-out.

``process="parallel"`` used to hand the whole task list to ``asyncio.gather``.
A crew built from a user-supplied task list therefore opened one simultaneous
provider call per task — a 429 storm and an unmetered cost spike at any
interesting cardinality. The ceiling comes from
``OrchestrationConfig.crew_max_parallel`` (default 8) and can be overridden
per crew.
"""

from __future__ import annotations

import asyncio

import pytest

from core.agent.crew import DEFAULT_CREW_MAX_PARALLEL, Crew, Task


class _SpyAgent:
    """Agent double recording peak concurrency across all instances."""

    live = 0
    peak = 0

    def __init__(self, delay: float = 0.02) -> None:
        self._delay = delay

    async def run(self, prompt: str):
        from core.agent.agent import AgentResult

        type(self).live += 1
        type(self).peak = max(type(self).peak, type(self).live)
        try:
            await asyncio.sleep(self._delay)
        finally:
            type(self).live -= 1
        return AgentResult(output=prompt, text=prompt)


@pytest.fixture(autouse=True)
def _reset_spy():
    _SpyAgent.live = 0
    _SpyAgent.peak = 0
    yield
    _SpyAgent.live = 0
    _SpyAgent.peak = 0


def _crew(count: int, **kwargs) -> Crew:
    agent = _SpyAgent()
    tasks = [Task(f"task {i}", agent=agent, name=f"t{i}") for i in range(count)]
    return Crew(agents=[agent], tasks=tasks, process="parallel", **kwargs)


class TestParallelFanOut:
    async def test_explicit_max_parallel_is_respected(self) -> None:
        crew = _crew(12, max_parallel=3)
        result = await crew.run()
        assert len(result.task_results) == 12
        assert _SpyAgent.peak <= 3

    async def test_results_stay_in_task_order(self) -> None:
        crew = _crew(6, max_parallel=2)
        result = await crew.run()
        assert [r.name for r in result.task_results] == [f"t{i}" for i in range(6)]

    async def test_default_limit_comes_from_settings(self, monkeypatch) -> None:
        import core.agent.crew as crew_mod

        monkeypatch.setattr(crew_mod, "_configured_max_parallel", lambda: 2)
        crew = _crew(8)
        await crew.run()
        assert _SpyAgent.peak <= 2

    async def test_default_constant_is_eight(self) -> None:
        assert DEFAULT_CREW_MAX_PARALLEL == 8

    async def test_non_positive_limit_falls_back_to_serial(self) -> None:
        crew = _crew(4, max_parallel=0)
        result = await crew.run()
        assert len(result.task_results) == 4
        assert _SpyAgent.peak == 1

    async def test_sequential_process_unaffected(self) -> None:
        agent = _SpyAgent()
        crew = Crew(
            agents=[agent],
            tasks=[Task(f"t{i}", agent=agent) for i in range(3)],
            process="sequential",
        )
        result = await crew.run()
        assert len(result.task_results) == 3
        assert _SpyAgent.peak == 1


class TestSettings:
    def test_orchestration_config_exposes_crew_max_parallel(self) -> None:
        from core.config.orchestration import OrchestrationConfig

        assert OrchestrationConfig().crew_max_parallel == 8


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
