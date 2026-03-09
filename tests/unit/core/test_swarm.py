"""
Unit Tests for Swarm Intelligence Module

Tests for auction, pheromones, team formation, and colony.
"""

import pytest
from core.swarm import (
    Colony,
    TaskAuction,
    PheromoneSystem,
    TeamFormationEngine,
    AgentProfile,
    Task,
    Bid,
)
from core.swarm.types import (
    AgentStatus,
    TaskPriority,
    Capability,
)


# ============================================================================
# AgentProfile Tests
# ============================================================================


class TestAgentProfile:
    """Tests for AgentProfile."""

    def test_creation(self):
        """Basic profile creation."""
        agent = AgentProfile(id="a1", name="Agent 1")

        assert agent.id == "a1"
        assert agent.status == AgentStatus.IDLE
        assert agent.is_available

    def test_capability_check(self):
        """Check capability matching."""
        agent = AgentProfile(
            id="a1",
            name="Agent 1",
            capabilities=[
                Capability(name="python", proficiency=0.9),
                Capability(name="analysis", proficiency=0.7),
            ],
        )

        assert agent.has_capability("python")
        assert agent.has_capability("python", min_proficiency=0.8)
        assert not agent.has_capability("java")

    def test_capability_score(self):
        """Calculate capability score."""
        agent = AgentProfile(
            id="a1",
            name="Agent 1",
            capabilities=[
                Capability(name="python", proficiency=0.8),
                Capability(name="analysis", proficiency=0.6),
            ],
        )

        score = agent.get_capability_score(["python", "analysis"])
        assert score == 0.7  # Average

    def test_availability(self):
        """Test availability check."""
        agent = AgentProfile(id="a1", name="Agent 1")

        assert agent.is_available

        agent.status = AgentStatus.BUSY
        assert not agent.is_available

        agent.status = AgentStatus.IDLE
        agent.current_load = 0.95
        assert not agent.is_available


# ============================================================================
# TaskAuction Tests
# ============================================================================


class TestTaskAuction:
    """Tests for TaskAuction."""

    def test_announce_task(self):
        """Announce task for bidding."""
        auction = TaskAuction()
        task = Task(id="t1", description="Test task")

        auction.announce_task(task)

        assert "t1" in auction.get_pending_auctions()

    def test_submit_bid(self):
        """Submit bid for task."""
        auction = TaskAuction()
        task = Task(id="t1", description="Test task")
        auction.announce_task(task)

        bid = Bid(agent_id="a1", task_id="t1", score=0.8)
        result = auction.submit_bid(bid)

        assert result is True
        assert len(auction.get_bids("t1")) == 1

    def test_reject_duplicate_bid(self):
        """Reject duplicate bids from same agent."""
        auction = TaskAuction()
        task = Task(id="t1", description="Test task")
        auction.announce_task(task)

        bid1 = Bid(agent_id="a1", task_id="t1", score=0.8)
        bid2 = Bid(agent_id="a1", task_id="t1", score=0.9)

        auction.submit_bid(bid1)
        result = auction.submit_bid(bid2)

        assert result is False
        assert len(auction.get_bids("t1")) == 1

    def test_resolve_auction(self):
        """Resolve auction with winner."""
        auction = TaskAuction()
        task = Task(id="t1", description="Test task")
        auction.announce_task(task)

        auction.submit_bid(Bid(agent_id="a1", task_id="t1", score=0.7))
        auction.submit_bid(Bid(agent_id="a2", task_id="t1", score=0.9))
        auction.submit_bid(Bid(agent_id="a3", task_id="t1", score=0.8))

        winner = auction.resolve("t1")

        assert winner == "a2"  # Highest score

    def test_calculate_bid(self):
        """Calculate optimal bid."""
        auction = TaskAuction()
        agent = AgentProfile(
            id="a1",
            name="Agent 1",
            capabilities=[Capability(name="python", proficiency=0.8)],
        )
        task = Task(
            id="t1",
            description="Python task",
            required_capabilities=["python"],
        )

        bid = auction.calculate_bid(agent, task)

        assert bid.agent_id == "a1"
        assert bid.task_id == "t1"
        assert bid.score > 0


# ============================================================================
# PheromoneSystem Tests
# ============================================================================


class TestPheromoneSystem:
    """Tests for PheromoneSystem."""

    def test_deposit_and_sense(self):
        """Deposit and sense pheromones."""
        system = PheromoneSystem()

        system.deposit("success", "location_a", intensity=1.0)
        signals = system.sense("location_a")

        assert "success" in signals
        assert signals["success"] == 1.0

    def test_reinforcement(self):
        """Multiple deposits reinforce pheromone."""
        system = PheromoneSystem()

        system.deposit("success", "location_a", intensity=1.0)
        system.deposit("success", "location_a", intensity=1.0)

        signals = system.sense("location_a")
        assert signals["success"] == 2.0

    def test_decay(self):
        """Pheromones decay over time."""
        system = PheromoneSystem(decay_rate=0.5)

        system.deposit("success", "location_a", intensity=1.0)
        system.decay_all()

        signals = system.sense("location_a")
        assert signals["success"] == 0.5

    def test_evaporate(self):
        """Evaporate pheromones at location."""
        system = PheromoneSystem()

        system.deposit("success", "location_a")
        system.deposit("failure", "location_a")
        system.evaporate("location_a", "success")

        signals = system.sense("location_a")
        assert "success" not in signals
        assert "failure" in signals

    def test_get_strongest(self):
        """Find strongest pheromone location."""
        system = PheromoneSystem()

        system.deposit("success", "loc_a", intensity=0.5)
        system.deposit("success", "loc_b", intensity=1.0)
        system.deposit("success", "loc_c", intensity=0.8)

        strongest = system.get_strongest("success")
        assert strongest == "loc_b"


# ============================================================================
# TeamFormationEngine Tests
# ============================================================================


class TestTeamFormationEngine:
    """Tests for TeamFormationEngine."""

    def test_form_team(self):
        """Form team for task."""
        agents = [
            AgentProfile(
                id="a1",
                name="Agent 1",
                capabilities=[Capability(name="analysis", proficiency=0.9)],
            ),
            AgentProfile(
                id="a2",
                name="Agent 2",
                capabilities=[Capability(name="analysis", proficiency=0.7)],
            ),
            AgentProfile(
                id="a3",
                name="Agent 3",
                capabilities=[Capability(name="writing", proficiency=0.8)],
            ),
        ]

        engine = TeamFormationEngine(agents=agents)
        task = Task(
            id="t1",
            description="Analysis task",
            required_capabilities=["analysis"],
        )

        team = engine.form_team(task)

        assert team is not None
        assert team.size >= 2
        assert "a1" in team.members or "a2" in team.members

    def test_leader_selection(self):
        """Team leader is selected."""
        agents = [
            AgentProfile(id="a1", name="Agent 1", success_rate=0.9),
            AgentProfile(id="a2", name="Agent 2", success_rate=0.7),
        ]

        engine = TeamFormationEngine(agents=agents)
        task = Task(id="t1", description="Task")

        team = engine.form_team(task)

        assert team.leader_id is not None

    def test_disband_team(self):
        """Disband team."""
        agents = [
            AgentProfile(id="a1", name="Agent 1"),
            AgentProfile(id="a2", name="Agent 2"),
        ]

        engine = TeamFormationEngine(agents=agents)
        team = engine.form_team(Task(id="t1", description="Task"))

        result = engine.disband_team(team.id)

        assert result is True
        assert engine.get_team(team.id) is None


# ============================================================================
# Colony Tests
# ============================================================================


class TestColony:
    """Tests for Colony."""

    def test_register_agent(self):
        """Register agent with colony."""
        colony = Colony()
        agent = AgentProfile(id="a1", name="Agent 1")

        colony.register_agent(agent)

        assert colony.get_agent("a1") is not None

    def test_get_available_agents(self):
        """Get available agents."""
        colony = Colony()
        colony.register_agent(AgentProfile(id="a1", name="Agent 1"))
        colony.register_agent(
            AgentProfile(id="a2", name="Agent 2", status=AgentStatus.BUSY)
        )

        available = colony.get_available_agents()

        assert len(available) == 1
        assert available[0].id == "a1"

    @pytest.mark.asyncio
    async def test_submit_task(self):
        """Submit task for allocation."""
        colony = Colony()
        colony.register_agent(
            AgentProfile(
                id="a1",
                name="Agent 1",
                capabilities=[Capability(name="python", proficiency=0.8)],
            )
        )

        task = Task(
            id="t1",
            description="Python task",
            required_capabilities=["python"],
        )

        winner = await colony.submit_task(task)

        assert winner == "a1"

    def test_complete_task(self):
        """Complete task and update state."""
        colony = Colony()
        agent = AgentProfile(id="a1", name="Agent 1")
        colony.register_agent(agent)

        task = Task(id="t1", description="Task", assigned_to="a1")
        colony._tasks[task.id] = task
        agent.status = AgentStatus.BUSY

        colony.complete_task("t1", success=True)

        assert colony._tasks["t1"].status == "completed"
        assert agent.status == AgentStatus.IDLE

    def test_get_stats(self):
        """Get colony statistics."""
        colony = Colony()
        colony.register_agent(AgentProfile(id="a1", name="Agent 1"))

        stats = colony.get_stats()

        assert "total_agents" in stats
        assert stats["total_agents"] == 1


# ============================================================================
# Integration Test
# ============================================================================


@pytest.mark.asyncio
async def test_full_swarm_workflow():
    """Full swarm coordination workflow."""
    # Setup colony
    colony = Colony()

    # Register diverse agents
    agents = [
        AgentProfile(
            id="researcher",
            name="Researcher",
            capabilities=[
                Capability(name="research", proficiency=0.9),
                Capability(name="analysis", proficiency=0.7),
            ],
        ),
        AgentProfile(
            id="writer",
            name="Writer",
            capabilities=[
                Capability(name="writing", proficiency=0.9),
                Capability(name="editing", proficiency=0.8),
            ],
        ),
        AgentProfile(
            id="analyst",
            name="Analyst",
            capabilities=[
                Capability(name="analysis", proficiency=0.95),
                Capability(name="research", proficiency=0.6),
            ],
        ),
    ]

    for agent in agents:
        colony.register_agent(agent)

    # Submit research task
    task = Task(
        id="research_task",
        description="Research market trends",
        required_capabilities=["research"],
        priority=TaskPriority.HIGH,
    )

    winner = await colony.submit_task(task)

    assert winner in ["researcher", "analyst"]

    # Complete task
    colony.complete_task(task.id, success=True)

    # Check pheromones were deposited
    signals = colony.pheromones.sense("task_type:research")
    assert signals.get(PheromoneSystem.SUCCESS, 0) > 0

    # Check stats
    stats = colony.get_stats()
    assert stats["completed_tasks"] == 1
