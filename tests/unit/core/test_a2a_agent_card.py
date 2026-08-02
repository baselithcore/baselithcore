"""
Unit Tests for Core A2A Module - Agent Card, Capabilities and Skills

Tests for the AgentCard descriptor plus its capability/skill members.
"""

from core.a2a import (
    AgentCapability,
    AgentCard,
)

# ============================================================================
# AgentCapability Tests
# ============================================================================


class TestAgentCapability:
    """Tests for AgentCapability dataclass."""

    def test_creation(self):
        """Basic creation."""
        cap = AgentCapability(
            name="search",
            description="Search capability",
        )

        assert cap.name == "search"
        assert cap.input_schema is None

    def test_creation_with_schemas(self):
        """Creation with schemas."""
        cap = AgentCapability(
            name="generate",
            description="Generate content",
            input_schema={
                "type": "object",
                "properties": {"prompt": {"type": "string"}},
            },
            output_schema={"type": "string"},
        )

        assert cap.input_schema is not None
        assert "prompt" in cap.input_schema["properties"]


# ============================================================================
# AgentCard Tests
# ============================================================================


class TestAgentCard:
    """Tests for AgentCard."""

    def test_creation(self):
        """Basic creation."""
        card = AgentCard(
            name="test_agent",
            description="A test agent",
        )

        assert card.name == "test_agent"
        assert card.version == "1.0.0"
        assert card.capabilities == []

    def test_creation_with_capabilities(self):
        """Creation with capabilities."""
        cap = AgentCapability(name="search", description="Search")
        card = AgentCard(
            name="search_agent",
            description="Search agent",
            capabilities=[cap],
        )

        assert len(card.capabilities) == 1

    def test_add_capability(self):
        """Add capability to card."""
        card = AgentCard(name="agent", description="An agent")

        card.add_capability(
            name="analyze",
            description="Analyze data",
        )

        assert len(card.capabilities) == 1
        assert card.capabilities[0].name == "analyze"

    def test_to_dict(self):
        """Convert to dictionary."""
        card = AgentCard(
            name="agent",
            description="Test agent",
            endpoint="http://localhost:8000",
        )
        card.add_capability("search", "Search capability")

        data = card.to_dict()

        assert data["name"] == "agent"
        assert data["endpoint"] == "http://localhost:8000"
        # capabilities is now the AgentCapabilities object
        assert "capabilities" in data
        assert "streaming" in data["capabilities"]
        # legacy capabilities are in legacyCapabilities
        assert len(data["legacyCapabilities"]) == 1

    def test_from_dict(self):
        """Create from dictionary."""
        data = {
            "name": "restored_agent",
            "description": "Restored from dict",
            "version": "2.0.0",
            "endpoint": "http://example.com",
            # New format: capabilities is AgentCapabilities object
            "capabilities": {"streaming": True},
            # Legacy capabilities go in legacyCapabilities
            "legacyCapabilities": [{"name": "cap1", "description": "Capability 1"}],
            "protocols": ["jsonrpc", "rest"],
        }

        card = AgentCard.from_dict(data)

        assert card.name == "restored_agent"
        assert card.version == "2.0.0"
        assert len(card.capabilities) == 1
        assert card.agentCapabilities.streaming is True

    def test_roundtrip_serialization(self):
        """to_dict and from_dict roundtrip."""
        original = AgentCard(
            name="roundtrip",
            description="Test roundtrip",
            endpoint="http://test.com",
        )
        original.add_capability("test", "Test cap")

        restored = AgentCard.from_dict(original.to_dict())

        assert restored.name == original.name
        assert restored.endpoint == original.endpoint


# ============================================================================
# AgentSkill Tests
# ============================================================================


class TestAgentSkill:
    """Tests for AgentSkill."""

    def test_creation(self):
        """Basic creation."""
        from core.a2a import AgentSkill

        skill = AgentSkill(
            id="search",
            name="Search",
            description="Search the web",
        )

        assert skill.id == "search"
        assert skill.name == "Search"
        assert skill.tags == []
        assert skill.inputModes == ["text/plain"]

    def test_with_tags_and_examples(self):
        """Creation with tags and examples."""
        from core.a2a import AgentSkill

        skill = AgentSkill(
            id="analyze",
            name="Analyze",
            description="Analyze data",
            tags=["data", "analysis"],
            examples=["Analyze this report", "What trends do you see?"],
        )

        assert len(skill.tags) == 2
        assert len(skill.examples) == 2

    def test_serialization(self):
        """Test to_dict and from_dict."""
        from core.a2a import AgentSkill

        original = AgentSkill(
            id="test",
            name="Test Skill",
            description="A test skill",
            tags=["test"],
        )

        data = original.to_dict()
        restored = AgentSkill.from_dict(data)

        assert restored.id == original.id
        assert restored.name == original.name


class TestAgentCardSkills:
    """Tests for AgentCard skill management."""

    def test_add_skill(self):
        """Add skill to card."""
        card = AgentCard(name="agent", description="Test")

        card.add_skill(
            id="search",
            name="Search",
            description="Search capability",
            tags=["search"],
        )

        assert len(card.skills) == 1
        assert card.skills[0].id == "search"

    def test_get_skill(self):
        """Get skill by ID."""
        card = AgentCard(name="agent", description="Test")
        card.add_skill("skill1", "Skill 1", "First skill")
        card.add_skill("skill2", "Skill 2", "Second skill")

        skill = card.get_skill("skill1")
        assert skill is not None
        assert skill.name == "Skill 1"

        missing = card.get_skill("nonexistent")
        assert missing is None

    def test_has_skill(self):
        """Check skill existence."""
        card = AgentCard(name="agent", description="Test")
        card.add_skill("present", "Present", "A skill")

        assert card.has_skill("present") is True
        assert card.has_skill("absent") is False
