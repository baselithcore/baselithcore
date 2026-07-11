"""PromptRegistry live wiring: render span emission + env-dir autoload."""

import pytest

import core.prompts.registry as registry_module
from core.prompts.registry import PromptRegistry, get_prompt_registry


class SpyTracer:
    def __init__(self, spans):
        self._spans = spans

    def start_span(self, name, attributes=None):
        self._spans.append((name, dict(attributes or {})))

        class _Span:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *args):
                return False

        return _Span()


def test_render_emits_prompt_identity_span(monkeypatch):
    spans = []
    import core.observability as obs

    monkeypatch.setattr(obs, "get_tracer", lambda name: SpyTracer(spans))

    registry = PromptRegistry()
    registry.register("greet", "Hello {{ name }}!", version="2")
    rendered = registry.render("greet", {"name": "Ada"})

    assert rendered.text == "Hello Ada!"
    assert len(spans) == 1
    name, attributes = spans[0]
    assert name == "prompt.render greet"
    assert attributes["prompt.name"] == "greet"
    assert attributes["prompt.version"] == "2"
    assert attributes["prompt.checksum"] == rendered.checksum


def test_global_registry_autoloads_from_env_dir(tmp_path, monkeypatch):
    (tmp_path / "welcome.md").write_text(
        "---\nname: welcome\nversion: '1'\n---\nWelcome {{ user }}!\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("BASELITH_PROMPTS_DIR", str(tmp_path))
    monkeypatch.setattr(registry_module, "_registry", None)  # reset singleton

    registry = get_prompt_registry()
    rendered = registry.render("welcome", {"user": "giovanni"})
    assert rendered.text == "Welcome giovanni!"

    monkeypatch.setattr(registry_module, "_registry", None)


def test_global_registry_without_env_is_empty(monkeypatch):
    monkeypatch.delenv("BASELITH_PROMPTS_DIR", raising=False)
    monkeypatch.setattr(registry_module, "_registry", None)

    registry = get_prompt_registry()
    assert registry.list_versions("anything") == []

    monkeypatch.setattr(registry_module, "_registry", None)


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
