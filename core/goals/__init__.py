"""
Goals Module — backward-compatible re-export.

The canonical implementation now lives in ``plugins.goals``.
This shim exists so that existing ``from core.goals import …`` imports
continue to work.
"""

from plugins.goals import Goal, GoalStatus, GoalTracker

__all__ = [
    "Goal",
    "GoalStatus",
    "GoalTracker",
]
