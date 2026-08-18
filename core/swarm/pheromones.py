"""
Stigmergic Signaling System.

Implements virtual pheromone mechanisms inspired by ant colony
optimization. Enables indirect, asynchronous communication between
agents by depositing and sensing digital markers in environmental
contexts.
"""

from collections import defaultdict
from datetime import datetime, timedelta

from core.observability.logging import get_logger

from .types import Pheromone

logger = get_logger(__name__)


class PheromoneSystem:
    """
    Controller for environmental signaling.

    Allows agents to leave persistent but decaying signals (pheromones)
    that influence the behavior of other agents. This pattern is crucial
    for decentralized discovery of successful paths (SUCCESS) or hazards
    (FAILURE/AVOID) without direct messaging overhead.
    """

    # Standard pheromone types
    SUCCESS = "success"
    FAILURE = "failure"
    HELP_NEEDED = "help_needed"
    AVOID = "avoid"
    EXPLORED = "explored"

    def __init__(
        self,
        decay_rate: float = 0.1,
        decay_interval: float = 1.0,
        max_intensity: float = 5.0,
    ):
        """
        Initialize pheromone system.

        Args:
            decay_rate: Rate of pheromone decay per interval
            decay_interval: Time between decay cycles (seconds)
            max_intensity: Maximum pheromone intensity at any location
        """
        self.decay_rate = decay_rate
        self.decay_interval = decay_interval
        self.max_intensity = max_intensity

        # Location -> Type -> Pheromone
        self._pheromones: dict[str, dict[str, Pheromone]] = defaultdict(dict)

        # Track active locations
        self._active_locations: set[str] = set()

    def deposit(
        self,
        ptype: str,
        location: str,
        intensity: float = 1.0,
        agent_id: str = "",
    ) -> None:
        """
        Deposit a pheromone.

        Args:
            ptype: Pheromone type (success, failure, help_needed, etc.)
            location: Context/location identifier
            intensity: Pheromone intensity
            agent_id: ID of depositing agent
        """
        existing = self._pheromones[location].get(ptype)

        if existing:
            # Reinforce existing pheromone
            existing.intensity = min(
                existing.intensity + intensity,
                self.max_intensity,
            )
            existing.timestamp = datetime.now()
            existing.depositor_id = agent_id
        else:
            # Create new pheromone
            self._pheromones[location][ptype] = Pheromone(
                type=ptype,
                location=location,
                intensity=min(intensity, self.max_intensity),
                depositor_id=agent_id,
            )

        self._active_locations.add(location)
        logger.debug(
            f"Pheromone deposited: {ptype} at {location}, intensity={intensity}"
        )

    def _apply_elapsed_decay(self, pheromone: Pheromone) -> None:
        """Apply time-based decay lazily, at read time.

        Nothing in the runtime schedules ``decay_all`` on a timer, so without
        lazy decay pheromones only ever accumulate — three failed tasks of one
        task_type permanently blocked every agent from bidding on it. Decaying
        on sense makes the signals self-healing with no background task: one
        ``decay_rate`` step per elapsed ``decay_interval``, with the timestamp
        advanced by the consumed whole intervals so the fractional remainder
        keeps accruing toward the next step.
        """
        elapsed = (datetime.now() - pheromone.timestamp).total_seconds()
        if elapsed < self.decay_interval:
            return
        steps = int(elapsed // self.decay_interval)
        pheromone.decay(self.decay_rate * steps)
        pheromone.timestamp += timedelta(seconds=steps * self.decay_interval)

    def _live_pheromones(self, location: str) -> dict[str, Pheromone]:
        """Decay, prune, and return the still-active pheromones at a location."""
        pheromones = self._pheromones.get(location)
        if not pheromones:
            return {}
        inactive = []
        for ptype, pheromone in pheromones.items():
            self._apply_elapsed_decay(pheromone)
            if not pheromone.is_active:
                inactive.append(ptype)
        for ptype in inactive:
            del pheromones[ptype]
        if not pheromones:
            self._pheromones.pop(location, None)
            self._active_locations.discard(location)
        return pheromones

    def sense(self, location: str) -> dict[str, float]:
        """
        Sense pheromones at a location.

        Args:
            location: Location to sense

        Returns:
            Dict of pheromone types to intensities
        """
        return {
            ptype: pheromone.intensity
            for ptype, pheromone in self._live_pheromones(location).items()
        }

    def sense_type(self, ptype: str) -> dict[str, float]:
        """
        Sense a specific pheromone type across all locations.

        Args:
            ptype: Pheromone type to sense

        Returns:
            Dict of locations to intensities
        """
        result = {}
        for location in list(self._pheromones):
            live = self._live_pheromones(location)
            if ptype in live:
                result[location] = live[ptype].intensity
        return result

    def get_strongest(
        self,
        ptype: str,
        exclude: set[str] | None = None,
    ) -> str | None:
        """
        Get location with strongest pheromone of a type.

        Args:
            ptype: Pheromone type
            exclude: Locations to exclude

        Returns:
            Location with strongest signal, or None
        """
        exclude = exclude or set()
        candidates = []

        for location in list(self._pheromones):
            if location in exclude:
                continue
            live = self._live_pheromones(location)
            if ptype in live:
                candidates.append((location, live[ptype].intensity))

        if not candidates:
            return None

        return max(candidates, key=lambda x: x[1])[0]

    def follow_gradient(
        self,
        current: str,
        ptype: str,
        neighbors: list[str],
    ) -> str | None:
        """
        Follow pheromone gradient to next location.

        Args:
            current: Current location
            ptype: Pheromone type to follow
            neighbors: Possible next locations

        Returns:
            Best next location based on gradient
        """
        current_intensity = self.sense(current).get(ptype, 0)
        best = None
        best_intensity = current_intensity

        for neighbor in neighbors:
            intensity = self.sense(neighbor).get(ptype, 0)
            if intensity > best_intensity:
                best_intensity = intensity
                best = neighbor

        return best

    def decay_all(self) -> None:
        """Apply decay to all pheromones."""
        to_remove = []

        for location in list(self._active_locations):
            pheromones = self._pheromones[location]
            inactive_types = []

            for ptype, pheromone in pheromones.items():
                pheromone.decay(self.decay_rate)
                if not pheromone.is_active:
                    inactive_types.append(ptype)

            # Remove inactive pheromones
            for ptype in inactive_types:
                del pheromones[ptype]

            # Remove empty locations
            if not pheromones:
                to_remove.append(location)

        for location in to_remove:
            del self._pheromones[location]
            self._active_locations.discard(location)

    def evaporate(self, location: str, ptype: str | None = None) -> None:
        """
        Evaporate pheromones at a location.

        Args:
            location: Location to evaporate
            ptype: Specific type to evaporate (None = all)
        """
        if location not in self._pheromones:
            return

        if ptype:
            if ptype in self._pheromones[location]:
                del self._pheromones[location][ptype]
        else:
            del self._pheromones[location]
            self._active_locations.discard(location)

    def get_active_locations(self) -> set[str]:
        """Get all locations with active pheromones."""
        return self._active_locations.copy()

    def get_stats(self) -> dict:
        """Get system statistics."""
        total_pheromones = sum(
            len(pheromones) for pheromones in self._pheromones.values()
        )
        return {
            "active_locations": len(self._active_locations),
            "total_pheromones": total_pheromones,
            "decay_rate": self.decay_rate,
        }
