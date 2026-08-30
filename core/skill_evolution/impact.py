"""Skill impact tracker: correlate skill activations with run outcomes.

Activation spans (``gen_ai.baselith.skill_name``) are emitted today but
never read back; this tracker closes that gap in-process. Attribution:

- With ``run_id``: activations are bucketed per run; an outcome carrying
  the same ``run_id`` credits exactly that bucket (then closes it).
- Without ``run_id``: a process-wide window of activations is credited by
  the next outcome and cleared — a v1 approximation that is only accurate
  for single-loop processes.

Open run buckets are LRU-capped so abandoned runs cannot leak memory.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Final

from core.skill_evolution.types import SkillImpact

__all__ = ["MAX_OPEN_RUNS", "SkillImpactTracker"]

#: Maximum run buckets held open awaiting an outcome (LRU-evicted).
MAX_OPEN_RUNS: Final[int] = 256


class SkillImpactTracker:
    """In-memory activation↔outcome correlation, keyed by skill name."""

    def __init__(self) -> None:
        self._impacts: dict[str, SkillImpact] = {}
        self._open_runs: OrderedDict[str, set[str]] = OrderedDict()
        self._window: set[str] = set()

    def record_activation(self, skill_name: str, run_id: str | None = None) -> None:
        """Record one activation of ``skill_name`` (optionally in a run)."""
        self._impact(skill_name).activations += 1
        if run_id is None:
            self._window.add(skill_name)
            return
        bucket = self._open_runs.setdefault(run_id, set())
        bucket.add(skill_name)
        self._open_runs.move_to_end(run_id)
        while len(self._open_runs) > MAX_OPEN_RUNS:
            self._open_runs.popitem(last=False)

    def record_outcome(self, score: float, run_id: str | None = None) -> None:
        """Credit an outcome score to the skills active in its scope."""
        if run_id is not None:
            skills = self._open_runs.pop(run_id, set())
        else:
            skills, self._window = self._window, set()
        for skill_name in skills:
            impact = self._impact(skill_name)
            impact.outcomes += 1
            impact.score_sum += score

    def stats(self) -> dict[str, SkillImpact]:
        """Snapshot of per-skill impact statistics (copies, safe to hold)."""
        return {name: impact.model_copy() for name, impact in self._impacts.items()}

    def _impact(self, skill_name: str) -> SkillImpact:
        return self._impacts.setdefault(skill_name, SkillImpact(skill_name=skill_name))
