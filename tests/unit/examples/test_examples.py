"""
Tests for example modules: showcase-patterns and api-integration.

These tests verify example structure and file contents.
"""

import pytest
from pathlib import Path


# Add examples to path for testing
# Path: tests/unit/examples/test_examples.py -> baselith-core/examples
EXAMPLES_PATH = Path(__file__).resolve().parent.parent.parent.parent / "examples"


class TestShowcasePatternsExample:
    """Tests for the showcase-patterns example."""

    @pytest.fixture
    def showcase_path(self):
        """Get path to showcase-patterns example."""
        return EXAMPLES_PATH / "showcase-patterns"

    def test_example_files_exist(self, showcase_path):
        """Test that all required example files exist."""
        required_files = [
            "main.py",
            "README.md",
            "requirements.txt",
        ]
        for file_name in required_files:
            file_path = showcase_path / file_name
            assert file_path.exists(), f"Missing file: {file_name}"

    def test_main_py_contains_patterns(self, showcase_path):
        """Test main.py contains expected pattern implementations."""
        main_path = showcase_path / "main.py"
        content = main_path.read_text()

        # Verify key patterns are implemented
        assert "class MemoryStore" in content
        assert "class Planner" in content
        assert "class Reflector" in content
        assert "class HumanInteraction" in content
        assert "class FeedbackCollector" in content

    def test_readme_lists_patterns(self, showcase_path):
        """Test README.md lists implemented patterns."""
        readme_path = showcase_path / "README.md"
        content = readme_path.read_text()

        # Verify pattern coverage is documented
        assert "Reflection" in content
        assert "Planning" in content
        assert "Memory" in content
        assert "Human-in-the-Loop" in content

    def test_main_has_fastapi_app(self, showcase_path):
        """Test main.py defines FastAPI application."""
        main_path = showcase_path / "main.py"
        content = main_path.read_text()

        assert "FastAPI(" in content
        assert "@app.post" in content or "@app.get" in content


class TestAPIIntegrationExample:
    """Tests for the api-integration example."""

    @pytest.fixture
    def api_path(self):
        """Get path to api-integration example."""
        return EXAMPLES_PATH / "api-integration"

    def test_example_files_exist(self, api_path):
        """Test that all required example files exist."""
        required_files = [
            "main.py",
            "README.md",
            "requirements.txt",
        ]
        for file_name in required_files:
            file_path = api_path / file_name
            assert file_path.exists(), f"Missing file: {file_name}"

    def test_main_py_contains_integration_components(self, api_path):
        """Test main.py contains expected integration components."""
        main_path = api_path / "main.py"
        content = main_path.read_text()

        # Verify key components are implemented
        assert "class RateLimiter" in content
        assert "class WebhookHandler" in content
        assert "class APIClient" in content

    def test_readme_lists_features(self, api_path):
        """Test README.md lists features."""
        readme_path = api_path / "README.md"
        content = readme_path.read_text()

        assert "Webhook" in content
        assert "Rate" in content

    def test_main_has_webhook_endpoint(self, api_path):
        """Test main.py has webhook endpoint."""
        main_path = api_path / "main.py"
        content = main_path.read_text()

        assert "/webhook" in content
        assert "verify_signature" in content


class TestResearchAssistantExample:
    """Tests for the research-assistant example."""

    @pytest.fixture
    def research_path(self):
        """Get path to research-assistant example."""
        return EXAMPLES_PATH / "research-assistant"

    def test_example_files_exist(self, research_path):
        """Test that all required example files exist."""
        required_files = [
            "main.py",
            "README.md",
            "requirements.txt",
        ]
        for file_name in required_files:
            file_path = research_path / file_name
            assert file_path.exists(), f"Missing file: {file_name}"


class TestDocumentAnalyzerExample:
    """Tests for the document-analyzer example."""

    @pytest.fixture
    def analyzer_path(self):
        """Get path to document-analyzer example."""
        return EXAMPLES_PATH / "document-analyzer"

    def test_example_files_exist(self, analyzer_path):
        """Test that all required example files exist."""
        required_files = [
            "main.py",
            "README.md",
            "requirements.txt",
        ]
        for file_name in required_files:
            file_path = analyzer_path / file_name
            assert file_path.exists(), f"Missing file: {file_name}"
