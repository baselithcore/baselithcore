"""
Goals Plugin

Goal tracking and monitoring for agent workflows.
Moved from baselith/core/goals/ — core contains only infrastructure.
"""

from baselith.core.plugins.compat_aliases import register_module_aliases

from .tracking.tracker import Goal, GoalStatus, GoalTracker

__all__ = [
    "GoalTracker",
    "Goal",
    "GoalStatus",
]

# The modules below moved into sub-packages (plugin layout standard); the old
# import paths stay importable so documented code and downstream plugins keep working.
register_module_aliases(
    {
        "tracker": "tracking.tracker",
    },
    packages=(__name__,),
)
