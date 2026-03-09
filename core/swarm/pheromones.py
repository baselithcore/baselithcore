"""
Stigmergic Signaling System.

Implements virtual pheromone mechanisms inspired by ant colony
optimization. Enables indirect, asynchronous communication between
agents by depositing and sensing digital markers in environmental
contexts.
"""

from core.observability.logging import get_logger
from typing import Dict, List, Optional, Set
from datetime import datetime
from collections import defaultdict

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
        self._pheromones: Dict[str, Dict[str, Pheromone]] = defaultdict(dict)

        # Track active locations
        self._active_locations: Set[str] = set()

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

    def sense(self, location: str) -> Dict[str, float]:
        """
        Sense pheromones at a location.

        Args:
            location: Location to sense

        Returns:
            Dict of pheromone types to intensities
        """
        if location not in self._pheromones:
            return {}

        return {
            ptype: pheromone.intensity
            for ptype, pheromone in self._pheromones[location].items()
            if pheromone.is_active
        }

    def sense_type(self, ptype: str) -> Dict[str, float]:
        """
        Sense a specific pheromone type across all locations.

        Args:
            ptype: Pheromone type to sense

        Returns:
            Dict of locations to intensities
        """
        result = {}
        for location, pheromones in self._pheromones.items():
            if ptype in pheromones and pheromones[ptype].is_active:
                result[location] = pheromones[ptype].intensity
        return result

    def get_strongest(
        self,
        ptype: str,
        exclude: Optional[Set[str]] = None,
    ) -> Optional[str]:
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

        for location, pheromones in self._pheromones.items():
            if location in exclude:
                continue
            if ptype in pheromones and pheromones[ptype].is_active:
                candidates.append((location, pheromones[ptype].intensity))

        if not candidates:
            return None

        return max(candidates, key=lambda x: x[1])[0]

    def follow_gradient(
        self,
        current: str,
        ptype: str,
        neighbors: List[str],
    ) -> Optional[str]:
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

    def evaporate(self, location: str, ptype: Optional[str] = None) -> None:
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

    def get_active_locations(self) -> Set[str]:
        """Get all locations with active pheromones."""
        return self._active_locations.copy()

    def get_stats(self) -> Dict:
        """Get system statistics."""
        total_pheromones = sum(
            len(pheromones) for pheromones in self._pheromones.values()
        )
        return {
            "active_locations": len(self._active_locations),
            "total_pheromones": total_pheromones,
            "decay_rate": self.decay_rate,
        }
