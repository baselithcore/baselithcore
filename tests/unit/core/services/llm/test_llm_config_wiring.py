"""Config surface for fallback chain + routing wiring."""

from core.config.services import LLMConfig


class TestLLMConfigWiring:
    def test_native_tools_default_on(self):
        assert LLMConfig().enable_native_tools is True

    def test_fallback_chain_default_empty(self):
        assert LLMConfig().fallback_chain == ""

    def test_fallback_chain_from_env(self, monkeypatch):
        monkeypatch.setenv("LLM_FALLBACK_CHAIN", "openai:gpt-4o-mini,ollama:llama3.2")
        assert LLMConfig().fallback_chain == "openai:gpt-4o-mini,ollama:llama3.2"

    def test_routing_defaults(self):
        config = LLMConfig()
        assert config.routing_enabled is False
        assert config.routing_policy == ""

    def test_routing_from_env(self, monkeypatch):
        monkeypatch.setenv("LLM_ROUTING_ENABLED", "true")
        monkeypatch.setenv("LLM_ROUTING_POLICY", '{"planning": "gpt-4o"}')
        config = LLMConfig()
        assert config.routing_enabled is True
        assert config.routing_policy == '{"planning": "gpt-4o"}'
