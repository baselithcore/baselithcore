"""Tests for config-driven ContextFolder wiring into AgentMemory."""

from __future__ import annotations

from core.config.memory import get_memory_runtime_config
from core.memory.folding import ContextFolder
from core.memory.manager import AgentMemory


def _clear_config_cache():
    get_memory_runtime_config.cache_clear()


class TestFolderWiring:
    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("MEMORY_CONTEXT_FOLDING_ENABLED", raising=False)
        _clear_config_cache()
        try:
            assert AgentMemory().context_folder is None
        finally:
            _clear_config_cache()

    def test_enabled_via_env(self, monkeypatch):
        monkeypatch.setenv("MEMORY_CONTEXT_FOLDING_ENABLED", "true")
        monkeypatch.setenv("MEMORY_CONTEXT_FOLD_THRESHOLD_CHARS", "1234")
        _clear_config_cache()
        try:
            memory = AgentMemory()
            assert isinstance(memory.context_folder, ContextFolder)
            assert memory.context_folder.config.fold_threshold_chars == 1234
        finally:
            _clear_config_cache()

    def test_explicit_folder_wins(self, monkeypatch):
        monkeypatch.delenv("MEMORY_CONTEXT_FOLDING_ENABLED", raising=False)
        _clear_config_cache()
        try:
            folder = ContextFolder()
            assert AgentMemory(context_folder=folder).context_folder is folder
        finally:
            _clear_config_cache()


class TestGetContextAsync:
    async def test_small_context_uses_verbatim_fast_path(self):
        """Below the fold threshold: no LLM summarization, content verbatim."""
        memory = AgentMemory(context_folder=ContextFolder())
        await memory.add_memory("short question")
        result = await memory.get_context_async()
        assert "short question" in result

    async def test_folding_failure_falls_back_to_truncation(self):
        class _BrokenFolder:
            async def fold_if_needed(self, history):
                raise RuntimeError("llm down")

        memory = AgentMemory(context_folder=_BrokenFolder())  # type: ignore[arg-type]
        await memory.add_memory("resilient")
        result = await memory.get_context_async()
        assert "resilient" in result
