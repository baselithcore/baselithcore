"""
Unit Tests for Core A2A Module - Agent Discovery and Registration

Tests for the peer registry, registration health tracking and discovery.
"""

import time

from core.a2a import (
    AgentCard,
    AgentDiscovery,
    AgentRegistration,
)

# ============================================================================
# AgentDiscovery Tests
# ============================================================================


class TestAgentDiscovery:
    """Tests for AgentDiscovery."""

    def test_initialization(self):
        """Basic initialization."""
        discovery = AgentDiscovery()

        assert len(discovery._agents) == 0

    def test_register(self):
        """Register an agent."""
        discovery = AgentDiscovery()
        card = AgentCard(name="agent1", description="First agent")

        discovery.register(card)

        assert discovery.get("agent1") == card

    def test_unregister_success(self):
        """Unregister existing agent."""
        discovery = AgentDiscovery()
        discovery.register(AgentCard(name="agent1", description="Test"))

        result = discovery.unregister("agent1")

        assert result is True
        assert discovery.get("agent1") is None

    def test_unregister_nonexistent(self):
        """Unregister nonexistent agent."""
        discovery = AgentDiscovery()

        result = discovery.unregister("nonexistent")

        assert result is False

    def test_find_by_capability(self):
        """Find agents by capability."""
        discovery = AgentDiscovery()

        card1 = AgentCard(name="searcher", description="Search agent")
        card1.add_capability("search", "Search the web")

        card2 = AgentCard(name="analyzer", description="Analysis agent")
        card2.add_capability("analyze", "Analyze data")

        card3 = AgentCard(name="multi", description="Multi-purpose")
        card3.add_capability("search", "Search")
        card3.add_capability("analyze", "Analyze")

        discovery.register(card1)
        discovery.register(card2)
        discovery.register(card3)

        searchers = discovery.find_by_capability("search")

        assert len(searchers) == 2
        names = [c.name for c in searchers]
        assert "searcher" in names
        assert "multi" in names

    def test_list_all(self):
        """List all registered agents."""
        discovery = AgentDiscovery()
        discovery.register(AgentCard(name="a1", description="Agent 1"))
        discovery.register(AgentCard(name="a2", description="Agent 2"))

        names = discovery.list_all()

        assert "a1" in names
        assert "a2" in names

    def test_get_all_cards(self):
        """Get all agent cards."""
        discovery = AgentDiscovery()
        discovery.register(AgentCard(name="a1", description="Agent 1"))
        discovery.register(AgentCard(name="a2", description="Agent 2"))

        cards = discovery.get_all_cards()

        assert len(cards) == 2


# ============================================================================
# Integration Test
# ============================================================================


def test_agent_discovery_workflow():
    """Full agent discovery workflow."""
    discovery = AgentDiscovery()

    # Create agents with capabilities
    qa_agent = AgentCard(
        name="qa_agent",
        description="Question answering agent",
        endpoint="http://qa-service:8000",
    )
    qa_agent.add_capability("answer", "Answer questions")
    qa_agent.add_capability("search", "Search knowledge base")

    gen_agent = AgentCard(
        name="generator",
        description="Content generation agent",
        endpoint="http://gen-service:8000",
    )
    gen_agent.add_capability("generate", "Generate content")

    # Register agents
    discovery.register(qa_agent)
    discovery.register(gen_agent)

    # Discover by capability
    search_capable = discovery.find_by_capability("search")
    assert len(search_capable) == 1
    assert search_capable[0].endpoint == "http://qa-service:8000"

    # Get agent for specific task
    generator = discovery.get("generator")
    assert generator is not None
    card_data = generator.to_dict()
    # Legacy capabilities are in legacyCapabilities
    assert "generate" in [c["name"] for c in card_data["legacyCapabilities"]]


# ============================================================================
# AgentRegistration Tests
# ============================================================================


class TestAgentRegistration:
    """Tests for AgentRegistration."""

    def test_creation(self):
        """Basic creation."""
        card = AgentCard(name="test", description="Test agent")
        reg = AgentRegistration(card=card)

        assert reg.card == card
        assert reg.is_healthy is True
        assert reg.failure_count == 0

    def test_heartbeat(self):
        """Test heartbeat update."""
        card = AgentCard(name="test", description="Test")
        reg = AgentRegistration(card=card)

        # Simulate time passing
        reg.last_seen = time.time() - 100

        reg.update_heartbeat()

        assert reg.seconds_since_seen < 1.0
        assert reg.is_healthy is True

    def test_failure_tracking(self):
        """Test failure tracking."""
        card = AgentCard(name="test", description="Test")
        reg = AgentRegistration(card=card)

        # Record failures
        reg.record_failure()
        assert reg.is_healthy is True  # Still healthy

        reg.record_failure()
        assert reg.is_healthy is True  # Still healthy

        reg.record_failure()
        assert reg.is_healthy is False  # Now unhealthy


# ============================================================================
# Enhanced AgentDiscovery Tests
# ============================================================================


class TestAgentDiscoveryHealth:
    """Tests for AgentDiscovery health features."""

    def test_heartbeat(self):
        """Test heartbeat update."""
        discovery = AgentDiscovery()
        discovery.register(AgentCard(name="agent1", description="Test"))

        result = discovery.heartbeat("agent1")
        assert result is True

        result = discovery.heartbeat("nonexistent")
        assert result is False

    def test_record_failure(self):
        """Test failure recording."""
        discovery = AgentDiscovery()
        discovery.register(AgentCard(name="agent1", description="Test"))

        # Record failures until unhealthy
        discovery.record_failure("agent1")
        discovery.record_failure("agent1")
        discovery.record_failure("agent1")

        reg = discovery.get_registration("agent1")
        assert reg is not None
        assert reg.is_healthy is False

    def test_list_healthy(self):
        """Test listing healthy agents."""
        discovery = AgentDiscovery()
        discovery.register(AgentCard(name="healthy1", description="Test"))
        discovery.register(AgentCard(name="healthy2", description="Test"))
        discovery.register(AgentCard(name="unhealthy", description="Test"))

        # Make one unhealthy
        for _ in range(3):
            discovery.record_failure("unhealthy")

        healthy = discovery.list_healthy()
        assert "healthy1" in healthy
        assert "healthy2" in healthy
        assert "unhealthy" not in healthy

    def test_find_by_capability_healthy_only(self):
        """Test finding by capability with health filter."""
        discovery = AgentDiscovery()

        card1 = AgentCard(name="healthy", description="Test")
        card1.add_capability("search", "Search")
        discovery.register(card1)

        card2 = AgentCard(name="unhealthy", description="Test")
        card2.add_capability("search", "Search")
        discovery.register(card2)

        # Make one unhealthy
        for _ in range(3):
            discovery.record_failure("unhealthy")

        # Healthy only (default)
        results = discovery.find_by_capability("search")
        assert len(results) == 1
        assert results[0].name == "healthy"

        # Include unhealthy
        results = discovery.find_by_capability("search", healthy_only=False)
        assert len(results) == 2

    def test_get_stats(self):
        """Test statistics."""
        discovery = AgentDiscovery()
        discovery.register(AgentCard(name="a1", description="Test"))
        discovery.register(AgentCard(name="a2", description="Test"))

        for _ in range(3):
            discovery.record_failure("a2")

        stats = discovery.get_stats()
        assert stats["total_agents"] == 2
        assert stats["healthy_agents"] == 1
        assert stats["unhealthy_agents"] == 1

    def test_event_callbacks(self):
        """Test registration/unregistration callbacks."""
        discovery = AgentDiscovery()
        registered = []
        unregistered = []

        discovery.on_register(lambda card: registered.append(card.name))
        discovery.on_unregister(lambda name: unregistered.append(name))

        discovery.register(AgentCard(name="agent1", description="Test"))
        discovery.unregister("agent1")

        assert "agent1" in registered
        assert "agent1" in unregistered
