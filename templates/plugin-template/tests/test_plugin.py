"""
Tests for {{PLUGIN_NAME}} plugin.
"""
import pytest
from plugin import {{PLUGIN_CLASS_NAME}}, plugin


class TestPlugin:
    """Test plugin functionality."""
    
    def test_plugin_name(self):
        """Verify plugin name is set."""
        assert plugin.name == "{{PLUGIN_SLUG}}"
    
    def test_plugin_version(self):
        """Verify plugin version."""
        assert plugin.version == "1.0.0"
    
    def test_initialize(self):
        """Test plugin initialization."""
        config = {"option1": "value1"}
        plugin.initialize(config)
        assert plugin._initialized is True
        assert plugin.config == config
    
    def test_shutdown(self):
        """Test plugin shutdown."""
        plugin.initialize({})
        plugin.shutdown()
        assert plugin._initialized is False
    
    def test_do_something(self):
        """Test example method."""
        result = plugin.do_something("test input")
        assert result == "test input"
