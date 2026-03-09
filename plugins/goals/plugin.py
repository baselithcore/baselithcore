"""Goals Plugin for BaselithCore.

Provides goal tracking and monitoring capabilities for agent workflows.
"""

from typing import Any, Dict, Optional
from core.plugins import Plugin
from .tracker import GoalTracker


class GoalsPlugin(Plugin):
    """
    Goals Tracking Plugin.

    Enables agents to define, track, and validate long-term goals.
    """

    def __init__(self):
        """Initialize the Goals plugin."""
        super().__init__()
        self.tracker: Optional[GoalTracker] = None

    async def initialize(self, config: Dict[str, Any]) -> None:
        """Initialize the Goals plugin."""
        await super().initialize(config)
        self.tracker = GoalTracker()
        print("🎯 Goals Plugin initialized.")

    async def shutdown(self) -> None:
        """Shutdown the Goals plugin."""
        self.tracker = None
        print("🎯 Goals Plugin shutting down.")
        await super().shutdown()

    def get_tracker(self) -> Optional[GoalTracker]:
        """Return the goal tracker instance."""
        return self.tracker
