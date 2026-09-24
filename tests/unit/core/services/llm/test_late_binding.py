"""A service from the shared funnel follows the pin at call time.

``get_llm_service()`` used to resolve the per-plugin pin once, when it was
called. A plugin that kept the result — an agent built at load time, a flow
handler, a module-level client — therefore ran on whatever was in force then
(usually the deployment default, since nothing is bound at load) and ignored
every pin an operator set afterwards from the Policy LLM console. Services the
funnel issues now re-resolve the pin on every call and forward it to the
service that pin selects; services built from an explicit config do not.
"""

from __future__ import annotations

import pytest

from core.config.services import LLMConfig
from core.context import reset_plugin_context, set_plugin_context
from core.services.llm.policy import PluginLLMPolicy, set_plugin_llm_policy_resolver
from core.services.llm.runtime import get_llm_service, reset_llm_service
from core.services.llm.service import LLMService

_SCRUB = (
    "LLM_PROVIDER",
    "LLM_MODEL",
    "LLM_API_KEY",
    "LLM_API_BASE",
    "LLM_OLLAMA_API_BASE",
    "OLLAMA_HOST",
    "LLM_OPENAI_API_KEY",
    "OPENAI_API_KEY",
    "LLM_VLLM_API_BASE",
    "LLM_VLLM_API_KEY",
    "VLLM_API_KEY",
)


class _FakeProvider:
    supports_native_tools = False
    supports_messages = False

    def __init__(self, config):
        self.label = f"{config.provider}/{config.model}"

    async def generate(self, prompt, model, json_mode=False, **_kw):
        return f"{self.label}|{model}", 3

    async def generate_stream(self, prompt, model, **_kw):
        yield f"{self.label}|{model}", 3

    async def generate_image(self, prompt, **_kw):
        from core.services.llm.images import GeneratedImage

        return GeneratedImage(data=b"x", media_type="image/png", model=self.label)


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    for var in _SCRUB:
        monkeypatch.delenv(var, raising=False)
    cfg = LLMConfig(
        provider="ollama", model="base-model", enable_cache=False, _env_file=None
    )
    monkeypatch.setattr("core.services.llm.runtime.get_llm_config", lambda: cfg)
    monkeypatch.setattr("core.services.llm.service.get_llm_config", lambda: cfg)
    monkeypatch.setattr("core.services.llm.service.create_provider", _FakeProvider)
    set_plugin_llm_policy_resolver(None)
    reset_llm_service()
    yield
    set_plugin_llm_policy_resolver(None)
    reset_llm_service()


def _pin(plugin: str, model: str) -> None:
    set_plugin_llm_policy_resolver(
        lambda name, _scope=None: (
            PluginLLMPolicy(provider="ollama", model=model) if name == plugin else None
        )
    )


class _InPlugin:
    def __init__(self, name: str):
        self.name = name

    def __enter__(self):
        self.token = set_plugin_context(self.name)

    def __exit__(self, *_exc):
        reset_plugin_context(self.token)


async def test_a_service_kept_from_load_time_follows_a_later_pin():
    kept = get_llm_service()  # resolved at "load", nothing bound, no pin
    _pin("planner", "pinned-model")

    with _InPlugin("planner"):
        text = await kept.generate_response("hi")

    assert text == "ollama/pinned-model|pinned-model"


async def test_a_re_pin_takes_effect_without_a_restart():
    kept = get_llm_service()
    _pin("planner", "first")
    with _InPlugin("planner"):
        assert (await kept.generate_response("hi")).endswith("|first")
    _pin("planner", "second")
    with _InPlugin("planner"):
        assert (await kept.generate_response("hi")).endswith("|second")


async def test_the_caller_decides_not_the_holder():
    """A service built inside one plugin serves another with that one's pin."""
    set_plugin_llm_policy_resolver(
        lambda name, _scope=None: PluginLLMPolicy(provider="ollama", model=f"m-{name}")
    )
    with _InPlugin("a"):
        kept = get_llm_service()
    with _InPlugin("b"):
        assert (await kept.generate_response("hi")).endswith("|m-b")


async def test_unpinned_callers_keep_the_default():
    kept = get_llm_service()
    _pin("planner", "pinned-model")
    with _InPlugin("someone-else"):
        assert (await kept.generate_response("hi")).endswith("|base-model")


async def test_streaming_follows_the_pin():
    kept = get_llm_service()
    _pin("planner", "pinned-model")
    with _InPlugin("planner"):
        chunks = [c async for c in kept.generate_response_stream("hi")]
    assert chunks and chunks[0].endswith("|pinned-model")


async def test_an_explicit_config_is_an_opt_out():
    """A plugin that builds its own LLMService(config=...) chose its target."""
    own = LLMService(
        config=LLMConfig(
            provider="ollama", model="own-model", enable_cache=False, _env_file=None
        )
    )
    _pin("planner", "pinned-model")
    with _InPlugin("planner"):
        assert (await own.generate_response("hi")).endswith("|own-model")


async def test_module_level_image_generation_follows_the_pin():
    from core.services.llm.images import generate_image

    kept = get_llm_service()
    _pin("planner", "pinned-model")
    with _InPlugin("planner"):
        image = await generate_image(kept, "a cat")
    assert image.model == "ollama/pinned-model"


async def test_module_level_batch_follows_the_pin():
    from core.services.llm.batch import BatchPrompt, generate_batch

    kept = get_llm_service()
    _pin("planner", "pinned-model")
    with _InPlugin("planner"):
        [done] = await generate_batch(kept, [BatchPrompt(custom_id="1", prompt="hi")])
    assert done.text.endswith("|pinned-model")
