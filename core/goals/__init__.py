"""
Goals Module — backward-compatible re-export.

The canonical implementation now lives in ``plugins.goals``.
This shim exists so that existing ``from core.goals import …`` imports
continue to work. Importing it emits a ``DeprecationWarning``.
"""

import warnings

from core.stability import _deprecation_message
from plugins.goals import Goal, GoalStatus, GoalTracker

# Module-level twin of ``@deprecated``: decorating the re-exported classes
# would patch the plugin's own classes and warn every plugin user too.
warnings.warn(
    _deprecation_message(
        "core.goals", since="0.39", removed_in="1.0", alternative="plugins.goals"
    ),
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "Goal",
    "GoalStatus",
    "GoalTracker",
]
