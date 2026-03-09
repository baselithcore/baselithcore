"""
Agent Discovery

Service for discovering and registering agents.
Includes health tracking, heartbeat, and filtered discovery.
"""

from core.observability.logging import get_logger
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable

from .agent_card import AgentCard

logger = get_logger(__name__)


@dataclass
class AgentRegistration:
    """
    Agent registration with health tracking.
    """

    card: AgentCard
    registered_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    is_healthy: bool = True
    failure_count: int = 0

    def update_heartbeat(self) -> None:
        """Update last seen timestamp."""
        self.last_seen = time.time()
        self.is_healthy = True
        self.failure_count = 0

    def record_failure(self) -> None:
        """Record a failure."""
        self.failure_count += 1
        if self.failure_count >= 3:
            self.is_healthy = False

    @property
    def seconds_since_seen(self) -> float:
        """Seconds since last heartbeat."""
        return time.time() - self.last_seen


class AgentDiscovery:
    """
    Agent discovery service with health tracking.

    Features:
    - Agent registration with health tracking
    - Discovery by capability
    - Filtered discovery (healthy only)
    - Heartbeat support
    - Agent lifecycle management
    """

    def __init__(self, stale_threshold: float = 300.0):
        """
        Initialize discovery service.

        Args:
            stale_threshold: Seconds before an agent is considered stale
        """
        self._agents: Dict[str, AgentRegistration] = {}
        self._stale_threshold = stale_threshold
        self._on_agent_registered: List[Callable[[AgentCard], None]] = []
        self._on_agent_unregistered: List[Callable[[str], None]] = []

    def register(self, card: AgentCard) -> None:
        """Register an agent."""
        self._agents[card.name] = AgentRegistration(card=card)
        logger.info(f"Registered agent: {card.name}")

        # Notify listeners
        for callback in self._on_agent_registered:
            try:
                callback(card)
            except Exception as e:
                logger.warning(f"Registration callback error: {e}")

    def unregister(self, name: str) -> bool:
        """Unregister an agent. Returns True if found."""
        if name in self._agents:
            del self._agents[name]
            logger.info(f"Unregistered agent: {name}")

            # Notify listeners
            for callback in self._on_agent_unregistered:
                try:
                    callback(name)
                except Exception as e:
                    logger.warning(f"Unregistration callback error: {e}")
            return True
        return False

    def get(self, name: str) -> Optional[AgentCard]:
        """Get agent card by name."""
        reg = self._agents.get(name)
        return reg.card if reg else None

    def get_registration(self, name: str) -> Optional[AgentRegistration]:
        """Get full registration info for an agent."""
        return self._agents.get(name)

    def heartbeat(self, name: str) -> bool:
        """
        Update agent heartbeat.

        Args:
            name: Agent name

        Returns:
            True if agent exists and was updated
        """
        reg = self._agents.get(name)
        if reg:
            reg.update_heartbeat()
            return True
        return False

    def record_failure(self, name: str) -> None:
        """Record a failure for an agent."""
        reg = self._agents.get(name)
        if reg:
            reg.record_failure()
            logger.warning(
                f"Agent {name} failure recorded "
                f"(count: {reg.failure_count}, healthy: {reg.is_healthy})"
            )

    def find_by_capability(
        self,
        capability: str,
        healthy_only: bool = True,
    ) -> List[AgentCard]:
        """
        Find agents with a specific capability.

        Args:
            capability: Capability name to search for
            healthy_only: Only return healthy agents
        """
        results = []
        for reg in self._agents.values():
            if healthy_only and not reg.is_healthy:
                continue
            if any(c.name == capability for c in reg.card.capabilities):
                results.append(reg.card)
        return results

    def find_by_protocol(
        self,
        protocol: str,
        healthy_only: bool = True,
    ) -> List[AgentCard]:
        """
        Find agents supporting a specific protocol.

        Args:
            protocol: Protocol name (e.g., "jsonrpc", "rest")
            healthy_only: Only return healthy agents
        """
        results = []
        for reg in self._agents.values():
            if healthy_only and not reg.is_healthy:
                continue
            if protocol in reg.card.protocols:
                results.append(reg.card)
        return results

    def list_all(self) -> List[str]:
        """List all registered agent names."""
        return list(self._agents.keys())

    def list_healthy(self) -> List[str]:
        """List only healthy agent names."""
        return [name for name, reg in self._agents.items() if reg.is_healthy]

    def get_all_cards(self, healthy_only: bool = False) -> List[AgentCard]:
        """
        Get all registered agent cards.

        Args:
            healthy_only: Only return healthy agents
        """
        if healthy_only:
            return [reg.card for reg in self._agents.values() if reg.is_healthy]
        return [reg.card for reg in self._agents.values()]

    def get_stale_agents(self) -> List[str]:
        """Get list of stale agents (no heartbeat within threshold)."""
        return [
            name
            for name, reg in self._agents.items()
            if reg.seconds_since_seen > self._stale_threshold
        ]

    def cleanup_stale(self) -> int:
        """
        Remove stale agents.

        Returns:
            Number of agents removed
        """
        stale = self.get_stale_agents()
        for name in stale:
            self.unregister(name)
        if stale:
            logger.info(f"Cleaned up {len(stale)} stale agents: {stale}")
        return len(stale)

    def on_register(self, callback: Callable[[AgentCard], None]) -> None:
        """Register a callback for agent registration events."""
        self._on_agent_registered.append(callback)

    def on_unregister(self, callback: Callable[[str], None]) -> None:
        """Register a callback for agent unregistration events."""
        self._on_agent_unregistered.append(callback)

    def get_stats(self) -> Dict:
        """Get discovery service statistics."""
        total = len(self._agents)
        healthy = len(self.list_healthy())
        stale = len(self.get_stale_agents())

        return {
            "total_agents": total,
            "healthy_agents": healthy,
            "unhealthy_agents": total - healthy,
            "stale_agents": stale,
        }
