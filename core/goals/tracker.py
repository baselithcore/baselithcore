"""
Hierarchical Goal Tracking and Management.

Provides backward-compatible access to the Goal Tracking system.
Manages the lifecycle of objectives, from decomposition into sub-goals
to progress monitoring and status reporting.
"""

from plugins.goals.tracker import Goal, GoalStatus, GoalTracker

__all__ = ["Goal", "GoalStatus", "GoalTracker"]
