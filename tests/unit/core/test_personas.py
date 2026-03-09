"""
Unit Tests for Core Personas Module

Tests for persona management.
"""

import pytest
from core.personas import PersonaManager, Persona
from core.personas.defaults import (
    HELPFUL_ASSISTANT,
    TECHNICAL_EXPERT,
    CREATIVE_WRITER,
)


# ============================================================================
# Persona Tests
# ============================================================================


class TestPersona:
    """Tests for Persona dataclass."""

    def test_creation(self):
        """Basic persona creation."""
        persona = Persona(
            name="test_persona",
            description="A test persona",
        )

        assert persona.name == "test_persona"
        assert persona.temperature == 0.7

    def test_creation_with_traits(self):
        """Persona with traits."""
        persona = Persona(
            name="expert",
            description="An expert",
            traits={"tone": "formal", "style": "detailed"},
        )

        assert persona.traits["tone"] == "formal"

    def test_get_prompt_prefix_with_system_prompt(self):
        """Get prefix when system_prompt is set."""
        persona = Persona(
            name="custom",
            description="Custom persona",
            system_prompt="You are a custom assistant.",
        )

        prefix = persona.get_prompt_prefix()

        assert prefix == "You are a custom assistant."

    def test_get_prompt_prefix_generated(self):
        """Get auto-generated prefix from traits."""
        persona = Persona(
            name="helper",
            description="A helpful assistant",
            traits={"tone": "friendly"},
        )

        prefix = persona.get_prompt_prefix()

        assert "helper" in prefix
        assert "helpful assistant" in prefix
        assert "friendly" in prefix


# ============================================================================
# PersonaManager Tests
# ============================================================================


@pytest.mark.asyncio
class TestPersonaManager:
    """Tests for PersonaManager."""

    async def test_initialization_empty(self):
        """Initialize without default."""
        manager = PersonaManager()

        assert await manager.get_active() is None

    async def test_initialization_with_default(self):
        """Initialize with default persona."""
        default = Persona(name="default", description="Default")
        manager = PersonaManager(default_persona=default)

        active = await manager.get_active()
        assert active.name == "default"

    async def test_register(self):
        """Register a persona."""
        manager = PersonaManager()
        persona = Persona(name="new", description="New persona")

        await manager.register(persona)

        retrieved = await manager.get("new")
        assert retrieved == persona

    async def test_switch_success(self):
        """Switch to registered persona."""
        manager = PersonaManager()
        await manager.register(Persona(name="p1", description="First"))
        await manager.register(Persona(name="p2", description="Second"))

        result = await manager.switch("p2")

        assert result is True
        active = await manager.get_active()
        assert active.name == "p2"

    async def test_switch_fail(self):
        """Switch to unregistered persona fails."""
        manager = PersonaManager()

        result = await manager.switch("nonexistent")

        assert result is False

    async def test_list_all(self):
        """List all registered personas."""
        manager = PersonaManager()
        await manager.register(Persona(name="a", description="A"))
        await manager.register(Persona(name="b", description="B"))

        names = await manager.list_all()

        assert "a" in names
        assert "b" in names


# ============================================================================
# Default Personas Tests
# ============================================================================


class TestDefaultPersonas:
    """Tests for pre-defined personas."""

    def test_helpful_assistant(self):
        """HELPFUL_ASSISTANT is valid."""
        assert HELPFUL_ASSISTANT.name == "helpful_assistant"
        assert HELPFUL_ASSISTANT.temperature == 0.7

    def test_technical_expert(self):
        """TECHNICAL_EXPERT has lower temperature."""
        assert TECHNICAL_EXPERT.name == "technical_expert"
        assert TECHNICAL_EXPERT.temperature == 0.5

    def test_creative_writer(self):
        """CREATIVE_WRITER has higher temperature."""
        assert CREATIVE_WRITER.name == "creative_writer"
        assert CREATIVE_WRITER.temperature == 0.9


# ============================================================================
# Integration Test
# ============================================================================


@pytest.mark.asyncio
async def test_persona_switching_workflow():
    """Full persona switching workflow."""
    manager = PersonaManager(default_persona=HELPFUL_ASSISTANT)

    # Register additional personas
    await manager.register(TECHNICAL_EXPERT)
    await manager.register(CREATIVE_WRITER)

    # Default is active
    active = await manager.get_active()
    assert active.name == "helpful_assistant"

    # Switch for technical context
    await manager.switch("technical_expert")
    active = await manager.get_active()
    assert active.temperature == 0.5

    # Switch for creative context
    await manager.switch("creative_writer")
    active = await manager.get_active()
    prefix = active.get_prompt_prefix()
    assert "creative" in prefix.lower()
