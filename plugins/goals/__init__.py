"""
Goals Plugin

Goal tracking and monitoring for agent workflows.
Moved from core/goals/ — core contains only infrastructure.
"""

from .tracker import GoalTracker, Goal, GoalStatus

__all__ = [
    "GoalTracker",
    "Goal",
    "GoalStatus",
]
