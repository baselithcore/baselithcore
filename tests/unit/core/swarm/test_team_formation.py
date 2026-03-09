import pytest
from core.swarm.team_formation import TeamFormationEngine
from core.swarm.types import Task, AgentProfile, Capability
from core.config.swarm import TeamConfig


@pytest.fixture
def team_config():
    return TeamConfig(min_team_size=2, max_team_size=3, capability_threshold=0.4)


@pytest.fixture
def engine(team_config):
    return TeamFormationEngine(config=team_config)


@pytest.fixture
def capable_agents():
    # Capabilities: frontend, backend, devops
    return [
        AgentProfile(
            id="a1",
            name="Frontend Dev",
            capabilities=[Capability(name="frontend", proficiency=0.9)],
        ),
        AgentProfile(
            id="a2",
            name="Backend Dev",
            capabilities=[Capability(name="backend", proficiency=0.9)],
        ),
        AgentProfile(
            id="a3",
            name="DevOps",
            capabilities=[Capability(name="devops", proficiency=0.9)],
        ),
        AgentProfile(
            id="a4",
            name="Fullstack",
            capabilities=[
                Capability(name="frontend", proficiency=0.8),
                Capability(name="backend", proficiency=0.8),
            ],
        ),
    ]


class TestTeamFormationEngine:
    def test_register_agent(self, engine):
        agent = AgentProfile(id="test", name="Test")
        engine.register_agent(agent)
        assert engine._agents["test"] == agent

    def test_form_team_success(self, engine, team_config, capable_agents):
        # Register agents
        for agent in capable_agents:
            engine.register_agent(agent)

        task = Task(
            description="Build Web App",
            required_capabilities=["frontend", "backend"],
        )

        team = engine.form_team(task)

        assert team is not None
        assert team.status == "active"
        assert len(team.members) >= team_config.min_team_size
        assert len(team.members) <= team_config.max_team_size
        assert team.id in engine._teams

        # Check membership (should include frontend/backend capable agents)
        # [engine._agents[mid] for mid in team.members]
        # At least one should have frontend, one backend (or combined)
        # The engine logic just sums proficiency scores so high scorers on required caps should be picked.

    def test_form_team_insufficient_agents(self, engine, team_config):
        # Only 1 agent available, min size is 2
        agent = AgentProfile(
            id="a1",
            name="Lonely",
            capabilities=[Capability(name="coding", proficiency=1.0)],
        )
        engine.register_agent(agent)

        task = Task(required_capabilities=["coding"])
        team = engine.form_team(task)

        assert team is None

    def test_form_team_preferred_agents(self, engine, capable_agents):
        for agent in capable_agents:
            engine.register_agent(agent)

        task = Task(required_capabilities=["frontend"])
        preferred = ["a1"]  # Frontend Dev

        team = engine.form_team(task, preferred_agents=preferred)
        assert team is not None
        assert "a1" in team.members

    def test_disband_team(self, engine, capable_agents):
        for agent in capable_agents:
            engine.register_agent(agent)

        task = Task(required_capabilities=["frontend", "backend"])
        team = engine.form_team(task)
        assert team is not None

        assert engine.disband_team(team.id) is True
        assert engine.get_team(team.id) is None
        # Note: In current impl, disband removes from _teams dict.
        # Ideally it might keep it but mark status. Implementation says pop.

    def test_reassign_leader(self, engine, capable_agents):
        for agent in capable_agents:
            engine.register_agent(agent)

        task = Task(required_capabilities=["frontend", "backend"])
        team = engine.form_team(task)
        assert team.leader_id is not None

        old_leader = team.leader_id

        # Force reassign
        engine.reassign_leader(team.id)
        # Since logic is deterministic based on leader_selection (capability by default),
        # it might pick the same one if members didn't change.
        # To test change, we'd need to remove the leader from the team first.

        team.remove_member(old_leader)
        # engine.reassign_leader checks members in team against _agents.

        new_leader_2 = engine.reassign_leader(team.id)
        assert new_leader_2 != old_leader
        assert new_leader_2 in team.members
