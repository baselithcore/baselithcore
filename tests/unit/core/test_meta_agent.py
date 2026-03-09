"""
Unit Tests for Meta-Agent Module

Tests for multi-persona ensemble reasoning.
"""

import pytest
from core.meta import (
    MultiPersonaAgent,
    PersonaEnsemble,
    InternalDebate,
    Perspective,
    DebateRound,
    DebateResult,
    MetaAgentResponse,
)
from core.meta.types import DebateRole, ConsensusLevel
from core.meta.ensemble import ADVOCATE_PERSONA, CRITIC_PERSONA, SYNTHESIZER_PERSONA
from core.personas import Persona


# ============================================================================
# Types Tests
# ============================================================================


class TestPerspective:
    """Tests for Perspective dataclass."""

    def test_creation(self):
        """Basic perspective creation."""
        perspective = Perspective(
            persona_name="test",
            role=DebateRole.ADVOCATE,
            content="Test content",
        )

        assert perspective.persona_name == "test"
        assert perspective.role == DebateRole.ADVOCATE
        assert perspective.confidence == 0.8

    def test_is_critical(self):
        """Check critical detection."""
        critic = Perspective(
            persona_name="devil",
            role=DebateRole.CRITIC,
            content="Critical view",
        )
        advocate = Perspective(
            persona_name="supporter",
            role=DebateRole.ADVOCATE,
            content="Supportive view",
        )

        assert critic.is_critical is True
        assert advocate.is_critical is False


class TestDebateRound:
    """Tests for DebateRound dataclass."""

    def test_has_movement_true(self):
        """Round with agreements has movement."""
        round_result = DebateRound(
            round_number=1,
            arguments=["Arg 1"],
            agreements=["We agree on X"],
        )

        assert round_result.has_movement is True

    def test_has_movement_false(self):
        """Round with no progress."""
        round_result = DebateRound(round_number=1)

        assert round_result.has_movement is False


class TestDebateResult:
    """Tests for DebateResult dataclass."""

    def test_reached_consensus_full(self):
        """Full consensus is reached."""
        result = DebateResult(
            rounds=[],
            consensus_level=ConsensusLevel.FULL,
        )

        assert result.reached_consensus is True

    def test_reached_consensus_partial(self):
        """Partial consensus not considered reached."""
        result = DebateResult(
            rounds=[],
            consensus_level=ConsensusLevel.PARTIAL,
        )

        assert result.reached_consensus is False


# ============================================================================
# PersonaEnsemble Tests
# ============================================================================


class TestPersonaEnsemble:
    """Tests for PersonaEnsemble class."""

    def test_default_initialization(self):
        """Default ensemble has multiple personas."""
        ensemble = PersonaEnsemble()

        assert len(ensemble.personas) >= 2
        assert "advocate" in ensemble.persona_names

    def test_initialization_with_devil_advocate(self):
        """Include devil's advocate by default."""
        ensemble = PersonaEnsemble(include_devil_advocate=True)

        assert any("devil" in name.lower() for name in ensemble.persona_names)

    def test_initialization_without_devil_advocate(self):
        """Can exclude devil's advocate."""
        ensemble = PersonaEnsemble(include_devil_advocate=False)

        assert not any("devil" in name.lower() for name in ensemble.persona_names)

    def test_custom_personas(self):
        """Custom personas override defaults."""
        custom = [
            Persona(name="expert1", description="Expert 1"),
            Persona(name="expert2", description="Expert 2"),
        ]
        ensemble = PersonaEnsemble(personas=custom)

        assert len(ensemble.personas) == 2
        assert "expert1" in ensemble.persona_names

    def test_role_assignment(self):
        """Roles are assigned based on persona names."""
        ensemble = PersonaEnsemble()

        # Critic should be assigned CRITIC role
        critic_role = ensemble.get_role("devils_advocate")
        assert critic_role == DebateRole.CRITIC

    @pytest.mark.asyncio
    async def test_generate_perspectives(self):
        """Generates perspectives for each persona."""
        ensemble = PersonaEnsemble()
        perspectives = await ensemble.generate_perspectives("Test question")

        assert len(perspectives) == len(ensemble.personas)
        assert all(isinstance(p, Perspective) for p in perspectives)

    def test_add_persona(self):
        """Can add persona dynamically."""
        ensemble = PersonaEnsemble()
        initial_count = len(ensemble.personas)

        new_persona = Persona(name="new_expert", description="New expert")
        ensemble.add_persona(new_persona, DebateRole.MEDIATOR)

        assert len(ensemble.personas) == initial_count + 1
        assert ensemble.get_role("new_expert") == DebateRole.MEDIATOR


# ============================================================================
# InternalDebate Tests
# ============================================================================


class TestInternalDebate:
    """Tests for InternalDebate class."""

    def test_initialization(self):
        """Default initialization."""
        debate = InternalDebate()

        assert debate.max_rounds == 3
        assert debate.consensus_threshold == 0.7

    @pytest.mark.asyncio
    async def test_run_empty_perspectives(self):
        """Empty perspectives return no consensus."""
        debate = InternalDebate()
        result = await debate.run([], "Test query")

        assert result.consensus_level == ConsensusLevel.NONE
        assert result.total_rounds == 0

    @pytest.mark.asyncio
    async def test_run_with_perspectives(self):
        """Run debate with valid perspectives."""
        debate = InternalDebate(max_rounds=2)

        perspectives = [
            Perspective(
                persona_name="advocate",
                role=DebateRole.ADVOCATE,
                content="This is a good idea",
            ),
            Perspective(
                persona_name="critic",
                role=DebateRole.CRITIC,
                content="Consider the risks",
            ),
        ]

        result = await debate.run(perspectives, "Should we proceed?")

        assert result.total_rounds >= 1
        assert isinstance(result.consensus_level, ConsensusLevel)

    def test_consensus_calculation(self):
        """Consensus calculated from round results."""
        debate = InternalDebate()

        # Simulate rounds with agreements
        rounds = [
            DebateRound(round_number=1, agreements=["Point 1", "Point 2"]),
        ]

        level = debate._calculate_consensus(rounds)
        assert level == ConsensusLevel.FULL


# ============================================================================
# MultiPersonaAgent Tests
# ============================================================================


class TestMultiPersonaAgent:
    """Tests for MultiPersonaAgent class."""

    def test_default_initialization(self):
        """Default agent has ensemble and debate."""
        agent = MultiPersonaAgent()

        assert agent.ensemble is not None
        assert agent.debate is not None

    def test_custom_components(self):
        """Can inject custom ensemble and debate."""
        custom_ensemble = PersonaEnsemble(include_devil_advocate=False)
        custom_debate = InternalDebate(max_rounds=5)

        agent = MultiPersonaAgent(
            ensemble=custom_ensemble,
            debate=custom_debate,
        )

        assert agent.debate.max_rounds == 5

    def test_persona_names(self):
        """Get persona names from agent."""
        agent = MultiPersonaAgent()

        names = agent.persona_names
        assert len(names) >= 2

    def test_add_persona(self):
        """Add persona to agent's ensemble."""
        agent = MultiPersonaAgent()
        initial = len(agent.persona_names)

        agent.add_persona(Persona(name="custom", description="Custom"))

        assert len(agent.persona_names) == initial + 1

    @pytest.mark.asyncio
    async def test_process(self):
        """Process query returns valid response."""
        agent = MultiPersonaAgent()

        response = await agent.process("Should we use microservices?")

        assert isinstance(response, MetaAgentResponse)
        assert response.final_answer
        assert response.perspective_count >= 2
        assert response.confidence >= 0.0

    @pytest.mark.asyncio
    async def test_process_with_context(self):
        """Process with additional context."""
        agent = MultiPersonaAgent()

        response = await agent.process(
            "What architecture should we use?",
            context={"team_size": 5, "budget": "medium"},
        )

        assert response.final_answer
        assert "query" in response.metadata

    def test_confidence_calculation(self):
        """Confidence based on consensus level."""
        agent = MultiPersonaAgent()

        full = DebateResult(rounds=[], consensus_level=ConsensusLevel.FULL)
        none = DebateResult(rounds=[], consensus_level=ConsensusLevel.NONE)

        assert agent._calculate_confidence(full) > agent._calculate_confidence(none)


# ============================================================================
# Integration Tests
# ============================================================================


@pytest.mark.asyncio
async def test_full_workflow():
    """Full multi-persona reasoning workflow."""
    # Create custom ensemble
    personas = [
        Persona(
            name="optimist",
            description="Sees opportunities",
            traits={"outlook": "positive"},
        ),
        Persona(
            name="pessimist",
            description="Sees risks",
            traits={"outlook": "cautious"},
        ),
        Persona(
            name="realist",
            description="Balances both",
            traits={"outlook": "balanced"},
        ),
    ]

    ensemble = PersonaEnsemble(personas=personas)
    debate = InternalDebate(max_rounds=2)
    agent = MultiPersonaAgent(ensemble=ensemble, debate=debate)

    response = await agent.process("Should we launch the new product next month?")

    assert response.perspective_count == 3
    assert response.debate_result.total_rounds >= 1
    assert response.final_answer


def test_default_personas_exist():
    """Verify default debate personas are defined."""
    assert ADVOCATE_PERSONA.name == "advocate"
    assert CRITIC_PERSONA.name == "devils_advocate"
    assert SYNTHESIZER_PERSONA.name == "synthesizer"
