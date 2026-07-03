from unittest.mock import patch

import pytest

from core.plugins.resource_analyzer import (
    ResourceAnalyzer,
    analyze_plugin_resources,
)


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

    def test_discover_plugin_extracts_static_capabilities(self, temp_plugins_dir):
        """Static discovery should expose capabilities without importing the plugin."""
        plugin_dir = temp_plugins_dir / "demo_plugin"
        plugin_dir.mkdir()
        (plugin_dir / "static").mkdir()
        (plugin_dir / "manifest.yaml").write_text(
            "\n".join(
                [
                    "name: demo-plugin",
                    "version: 1.2.3",
                    "description: Demo plugin",
                    "author: Test",
                ]
            )
        )
        (plugin_dir / "plugin.py").write_text(
            """
from core.plugins import RouterPlugin


class DemoPlugin(RouterPlugin):
    def get_router_prefix(self):
        return "/api/custom-demo"

    def get_intent_patterns(self):
        return [
            {"name": "demo_intent", "patterns": ["demo"], "priority": 2},
        ]

    def get_flow_handlers(self):
        return {
            "demo_intent": object(),
        }

    def register_entity_types(self):
        return [
            {"type": "demo_entity", "schema": {"title": str}},
        ]

    def register_relationship_types(self):
        return [
            {"type": "DEMO_REL", "source_types": ["demo_entity"], "target_types": ["demo_entity"]},
        ]

    def get_stylesheets(self):
        return ["demo.css"]

    def get_scripts(self):
        return ["demo.js"]

    def get_ui_tabs(self):
        return [{"id": "demo", "label": "Demo"}]
"""
        )

        analyzer = ResourceAnalyzer(temp_plugins_dir)
        discovery = analyzer.discover_plugin(plugin_dir)

        assert discovery is not None
        assert discovery.name == "demo-plugin"
        assert discovery.directory_name == "demo_plugin"
        assert discovery.provides_routes is True
        assert discovery.router_prefix == "/api/custom-demo"
        assert "demo_intent" in discovery.intent_patterns
        assert discovery.flow_handler_names == ["demo_intent"]
        assert "demo_entity" in discovery.entity_types
        assert "DEMO_REL" in discovery.relationship_types
        assert discovery.stylesheets == ["demo.css"]
        assert discovery.scripts == ["demo.js"]
        assert discovery.ui_tabs == [{"id": "demo", "label": "Demo"}]
        assert discovery.static_path == plugin_dir / "static"

    def test_discover_plugins_resolves_directory_aliases(self, temp_plugins_dir):
        """Configs keyed by directory name should still discover metadata-name plugins."""
        plugin_dir = temp_plugins_dir / "reasoning_agent"
        plugin_dir.mkdir()
        (plugin_dir / "manifest.yaml").write_text(
            "\n".join(
                [
                    "name: reasoning-agent",
                    "version: 0.1.0",
                    "description: Reasoning plugin",
                ]
            )
        )
        (plugin_dir / "plugin.py").write_text(
            """
from core.plugins import AgentPlugin


class ReasoningPlugin(AgentPlugin):
    def get_intent_patterns(self):
        return [{"name": "reasoning", "patterns": ["reason"]}]
"""
        )

        analyzer = ResourceAnalyzer(temp_plugins_dir)
        discoveries = analyzer.discover_plugins({"reasoning_agent": {"enabled": True}})

        assert "reasoning-agent" in discoveries
        assert discoveries["reasoning-agent"].directory_name == "reasoning_agent"
