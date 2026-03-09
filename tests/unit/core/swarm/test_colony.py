import pytest

from core.swarm.colony import Colony
from core.swarm.types import (
    Task,
    AgentProfile,
    MessageType,
    SwarmMessage,
    Capability,
    AgentStatus,
)
from core.config.swarm import SwarmConfig
from core.swarm.pheromones import PheromoneSystem


@pytest.fixture
def swarm_config():
    return SwarmConfig()


@pytest.fixture
def colony(swarm_config):
    return Colony(config=swarm_config)


@pytest.fixture
def agents():
    return [
        AgentProfile(
            id="a1",
            name="Agent 1",
            capabilities=[Capability(name="cap1", proficiency=0.9)],
            success_rate=0.9,
        ),
        AgentProfile(
            id="a2",
            name="Agent 2",
            capabilities=[Capability(name="cap1", proficiency=0.7)],
            success_rate=0.9,
        ),
    ]


@pytest.mark.asyncio
class TestColony:
    async def test_register_agent(self, colony):
        agent = AgentProfile(id="test", name="Test")
        colony.register_agent(agent)
        assert colony.get_agent("test") == agent
        assert len(colony.get_available_agents()) == 1

    async def test_unregister_agent(self, colony):
        agent = AgentProfile(id="test", name="Test")
        colony.register_agent(agent)
        colony.unregister_agent("test")
        assert colony.get_agent("test") is None

    async def test_submit_task(self, colony, agents):
        for agent in agents:
            colony.register_agent(agent)

        task = Task(description="Test Task", required_capabilities=["cap1"])

        # Test full flow: announce -> bid -> resolve
        winner_id = await colony.submit_task(task)

        assert winner_id is not None
        assert winner_id in [a.id for a in agents]
        assert colony.get_agent(winner_id).status == AgentStatus.BUSY
        assert task.assigned_to == winner_id
        assert task.status == "assigned"

        # No pheromone yet — deposited only on complete_task
        signals = colony.pheromones.sense("task_type:cap1")
        assert signals.get(PheromoneSystem.SUCCESS, 0) == 0

    async def test_complete_task(self, colony, agents):
        for agent in agents:
            colony.register_agent(agent)

        task = Task(description="Test Task", required_capabilities=["cap1"])
        winner_id = await colony.submit_task(task)

        colony.complete_task(task.id, success=True, result="Done")

        assert colony._tasks[task.id].status == "completed"
        assert colony.get_agent(winner_id).status == AgentStatus.IDLE
        assert colony.get_agent(winner_id).success_rate > 0.9  # Initial was 0.9 -> 0.91

    async def test_request_help(self, colony, agents):
        # a1 is requester, a2 is helper
        colony.register_agent(agents[0])
        colony.register_agent(agents[1])

        task = Task(description="Hard Task", id="t1")
        colony._tasks["t1"] = task

        helper_id = await colony.request_help(
            agent_id="a1", task_id="t1", capabilities_needed=["cap1"]
        )

        assert helper_id == "a2"

        # Check help needed pheromone
        signals = colony.pheromones.sense("task:t1")
        assert signals.get(PheromoneSystem.HELP_NEEDED, 0) > 0

    async def test_messaging(self, colony):
        received = []

        def handler(msg):
            received.append(msg)

        colony.on_message(MessageType.HEARTBEAT, handler)

        msg = SwarmMessage(type=MessageType.HEARTBEAT, payload={"status": "ok"})
        colony.broadcast_message(msg)

        assert len(received) == 1
        assert received[0] == msg

    async def test_self_heal(self, colony, agents):
        colony.register_agent(agents[0])

        # Manually assign task and make agent offline
        task = Task(id="t1", assigned_to="a1", status="assigned")
        colony._tasks["t1"] = task

        agents[0].status = AgentStatus.OFFLINE

        colony.self_heal()

        assert task.assigned_to is None
        assert task.status == "pending"
        # Since it was re-announced, it should be in pending auctions
        assert task.id in colony.auction.get_pending_auctions()
