import pytest
from unittest.mock import patch
from core.plugins.resource_analyzer import ResourceAnalyzer, analyze_plugin_resources


@pytest.fixture
def temp_plugins_dir(tmp_path):
    """Create a temporary plugins directory for testing."""
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    return plugins_dir


class TestResourceAnalyzer:
    def test_manifest_priority(self, temp_plugins_dir):
        """Test that manifest.yaml has priority over yml and json."""
        plugin_name = "test_plugin"
        plugin_dir = temp_plugins_dir / plugin_name
        plugin_dir.mkdir()

        # Create multiple manifest files
        (plugin_dir / "manifest.json").write_text(
            '{"name": "json_plugin", "required_resources": ["postgres"]}'
        )
        (plugin_dir / "manifest.yml").write_text(
            'name: yml_plugin\nrequired_resources: ["redis"]'
        )
        (plugin_dir / "manifest.yaml").write_text(
            'name: yaml_plugin\nrequired_resources: ["llm"]'
        )

        analyzer = ResourceAnalyzer(temp_plugins_dir)
        metadata = analyzer.get_plugin_metadata(plugin_name)

        assert metadata is not None
        assert metadata.name == "yaml_plugin"
        assert "llm" in metadata.required_resources

    def test_analyze_requirements(self, temp_plugins_dir):
        """Test analyzing requirements from multiple plugins."""
        # Plugin 1: requires postgres, optional redis
        p1_dir = temp_plugins_dir / "p1"
        p1_dir.mkdir()
        (p1_dir / "manifest.yaml").write_text(
            "name: p1\nrequired_resources: [postgres]\noptional_resources: [redis]"
        )

        # Plugin 2: requires redis, optional graph
        p2_dir = temp_plugins_dir / "p2"
        p2_dir.mkdir()
        (p2_dir / "manifest.yaml").write_text(
            "name: p2\nrequired_resources: [redis]\noptional_resources: [graph]"
        )

        configs = {
            "p1": {"enabled": True},
            "p2": {"enabled": True},
            "p3": {"enabled": False},  # Disabled should be skipped
        }

        analyzer = ResourceAnalyzer(temp_plugins_dir)
        requirements = analyzer.analyze_requirements(configs)

        assert "postgres" in requirements["required"]
        assert "redis" in requirements["required"]
        assert "graph" in requirements["optional"]
        # Redis is required by p2, so it shouldn't be in optional even if p1 says so
        assert "redis" not in requirements["optional"]

    def test_init_order_topological_sort(self, temp_plugins_dir):
        """Test resource initialization order with dependencies."""
        analyzer = ResourceAnalyzer(temp_plugins_dir)

        resources = {"memory", "redis", "graph", "vectorstore"}
        order = analyzer.get_resource_init_order(resources)

        assert order.index("redis") < order.index("graph")
        assert order.index("redis") < order.index("memory")
        assert order.index("vectorstore") < order.index("memory")

    def test_circular_dependency(self, temp_plugins_dir):
        """Test circular dependency detection."""
        analyzer = ResourceAnalyzer(temp_plugins_dir)

        # Induce circular dependency
        with patch.object(
            ResourceAnalyzer, "DEFAULT_DEPENDENCIES", {"a": ["b"], "b": ["a"]}
        ):
            with pytest.raises(ValueError, match="Circular dependency detected"):
                analyzer.get_resource_init_order({"a", "b"})

    def test_complex_init_order(self, temp_plugins_dir):
        analyzer = ResourceAnalyzer(temp_plugins_dir)
        complex_resources = {
            "evolution",
            "evaluation",
            "memory",
            "llm",
            "vectorstore",
            "postgres",
            "redis",
        }
        order = analyzer.get_resource_init_order(complex_resources)
        assert order.index("postgres") < order.index("vectorstore")
        assert order.index("vectorstore") < order.index("memory")
        assert order.index("llm") < order.index("evaluation")
        assert order.index("evaluation") < order.index("evolution")

    def test_convenience_function(self, temp_plugins_dir):
        """Test the convenience function."""
        p1_dir = temp_plugins_dir / "p1"
        p1_dir.mkdir()
        (p1_dir / "manifest.yaml").write_text(
            "name: p1\nrequired_resources: [postgres]"
        )

        result = analyze_plugin_resources(temp_plugins_dir, {"p1": {"enabled": True}})
        assert "postgres" in result["required"]
