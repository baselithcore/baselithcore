"""
Tests for template modules: rag-system and baselith-core-collab.

These tests verify template structure and file contents.
"""

import pytest
import yaml
from pathlib import Path


# Add templates to path for testing
# Path: tests/unit/templates/test_templates.py -> baselith-core/templates
TEMPLATES_PATH = Path(__file__).resolve().parent.parent.parent.parent / "templates"


class TestRAGSystemTemplate:
    """Tests for the RAG system template."""

    @pytest.fixture
    def rag_system_path(self):
        """Get path to rag-system template."""
        return TEMPLATES_PATH / "rag-system"

    def test_template_files_exist(self, rag_system_path):
        """Test that all required template files exist."""
        required_files = [
            "main.py",
            "README.md",
            "config.yaml",
            "docker-compose.yml",
            "requirements.txt",
            ".env.example",
        ]
        for file_name in required_files:
            file_path = rag_system_path / file_name
            assert file_path.exists(), f"Missing file: {file_name}"

    def test_config_yaml_structure(self, rag_system_path):
        """Test config.yaml has required sections."""
        config_path = rag_system_path / "config.yaml"
        with open(config_path) as f:
            config = yaml.safe_load(f)

        assert "llm" in config
        assert "vectorstore" in config
        assert "ingestion" in config
        assert "retrieval" in config

    def test_docker_compose_services(self, rag_system_path):
        """Test docker-compose.yml has required services."""
        compose_path = rag_system_path / "docker-compose.yml"
        with open(compose_path) as f:
            compose = yaml.safe_load(f)

        assert "services" in compose
        assert "qdrant" in compose["services"]
        assert "redis" in compose["services"]

    def test_main_py_contains_rag_components(self, rag_system_path):
        """Test main.py contains expected RAG components."""
        main_path = rag_system_path / "main.py"
        content = main_path.read_text()

        # Verify key components are defined
        assert "class RAGSystem" in content
        assert "FastAPI" in content

    def test_readme_has_documentation(self, rag_system_path):
        """Test README.md has proper documentation."""
        readme_path = rag_system_path / "README.md"
        content = readme_path.read_text()

        assert "Quick Start" in content
        assert "Configuration" in content
        assert "API Endpoints" in content


class TestMultiAgentCollabTemplate:
    """Tests for the baselith-core-collab template."""

    @pytest.fixture
    def collab_path(self):
        """Get path to multi-agent-collab template."""
        return TEMPLATES_PATH / "multi-agent-collab"

    def test_template_files_exist(self, collab_path):
        """Test that all required template files exist."""
        required_files = [
            "main.py",
            "README.md",
            "config.yaml",
            "requirements.txt",
        ]
        for file_name in required_files:
            file_path = collab_path / file_name
            assert file_path.exists(), f"Missing file: {file_name}"

    def test_config_yaml_agents(self, collab_path):
        """Test config.yaml has agent definitions."""
        config_path = collab_path / "config.yaml"
        with open(config_path) as f:
            config = yaml.safe_load(f)

        assert "agents" in config
        assert "researcher" in config["agents"]
        assert "writer" in config["agents"]
        assert "reviewer" in config["agents"]

    def test_main_py_contains_agents(self, collab_path):
        """Test main.py contains expected agent components."""
        main_path = collab_path / "main.py"
        content = main_path.read_text()

        # Verify key components are defined
        assert "class ResearcherAgent" in content
        assert "class WriterAgent" in content
        assert "class ReviewerAgent" in content
        assert "class OrchestratorAgent" in content
        assert "class AgentRole" in content

    def test_readme_has_architecture(self, collab_path):
        """Test README.md has architecture documentation."""
        readme_path = collab_path / "README.md"
        content = readme_path.read_text()

        assert "Architecture" in content
        assert "Workflow" in content


class TestPluginTemplate:
    """Tests for the plugin template."""

    @pytest.fixture
    def plugin_path(self):
        """Get path to plugin-template."""
        return TEMPLATES_PATH / "plugin-template"

    def test_template_files_exist(self, plugin_path):
        """Test that all required template files exist."""
        required_files = [
            "plugin.py",
            "README.md",
            "models.py",
            "router.py",
        ]
        for file_name in required_files:
            file_path = plugin_path / file_name
            assert file_path.exists(), f"Missing file: {file_name}"

    def test_plugin_py_has_base_structure(self, plugin_path):
        """Test plugin.py has required structure."""
        plugin_py = plugin_path / "plugin.py"
        content = plugin_py.read_text()

        assert "AgentPlugin" in content
        assert "def initialize" in content
        assert "def shutdown" in content
        assert "plugin =" in content  # Export


class TestCustomAgentTemplate:
    """Tests for the custom-agent template."""

    @pytest.fixture
    def agent_path(self):
        """Get path to custom-agent-template."""
        return TEMPLATES_PATH / "custom-agent-template"

    def test_template_files_exist(self, agent_path):
        """Test that all required template files exist."""
        required_files = [
            "agent.py",
            "README.md",
            "tools.py",
        ]
        for file_name in required_files:
            file_path = agent_path / file_name
            assert file_path.exists(), f"Missing file: {file_name}"
