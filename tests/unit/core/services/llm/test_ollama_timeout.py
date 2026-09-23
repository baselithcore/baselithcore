"""An Ollama service keeps the deadline its own config gives it.

Every other provider took ``request_timeout`` from the config it was built
with; Ollama alone read the process-wide default. A plugin pinned to a slow
local model — a 27B reading a whole CV under a JSON schema — therefore timed
out at the global deadline however long its own config allowed, and each lost
pass cost the plugin its majority. The failure read as a slow model, not as a
setting that was being ignored.
"""

from unittest.mock import patch

from core.config.services import LLMConfig
from core.services.llm.provider_factory import create_provider
from core.services.llm.providers.ollama_provider import OllamaProvider


def _config(**kwargs) -> LLMConfig:
    """A config built from explicit values only, no ambient environment."""
    with patch.dict("os.environ", {}, clear=True):
        return LLMConfig(**kwargs)


def test_a_service_config_sets_the_ollama_deadline() -> None:
    config = _config(
        provider="ollama",
        model="qwen3.8:27b",
        api_base="http://box:11434",
        request_timeout=600.0,
        connect_timeout=7.0,
    )
    provider = create_provider(config)
    assert isinstance(provider, OllamaProvider)
    assert provider._timeout.read == 600.0
    assert provider._timeout.connect == 7.0


def test_without_explicit_timeouts_the_global_default_still_applies() -> None:
    """Callers that build the provider directly keep today's behaviour."""
    ambient = _config(provider="ollama", model="m", request_timeout=42.0)
    with patch("core.config.services.get_llm_config", return_value=ambient):
        provider = OllamaProvider(api_base="http://box:11434")
    assert provider._timeout.read == 42.0
