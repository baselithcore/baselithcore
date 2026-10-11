"""Scoped configuration overrides (``override_config``) and their coverage gate."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import pkgutil
import typing

import pytest
from pydantic import BaseModel

import baselith.core.config as config_pkg
from baselith.core.config import (
    LLMConfig,
    StorageConfig,
    get_llm_config,
    get_storage_config,
    override_config,
)
from baselith.core.config.memory import (
    MemoryRuntimeConfig,
    get_memory_runtime_config,
)
from baselith.core.config.overrides import active_override


class _DerivedLLMConfig(LLMConfig):
    """A subclass must answer for its base accessor."""


class TestScope:
    def test_accessor_returns_the_override_inside_the_block_only(self) -> None:
        cached = get_llm_config()
        injected = LLMConfig(model="override-model")

        with override_config(injected):
            assert get_llm_config() is injected
            assert active_override(LLMConfig) is injected

        assert get_llm_config() is cached
        assert active_override(LLMConfig) is None

    def test_other_accessors_are_untouched(self) -> None:
        cached_storage = get_storage_config()

        with override_config(LLMConfig(model="m")):
            assert get_storage_config() is cached_storage

    def test_blocks_nest_and_the_inner_one_wins(self) -> None:
        outer_llm = LLMConfig(model="outer")
        storage = StorageConfig()
        inner_llm = LLMConfig(model="inner")

        with override_config(outer_llm, storage):
            with override_config(inner_llm):
                assert get_llm_config() is inner_llm
                assert get_storage_config() is storage
            assert get_llm_config() is outer_llm

    def test_an_exception_still_restores_the_previous_scope(self) -> None:
        cached = get_llm_config()
        with pytest.raises(RuntimeError):
            with override_config(LLMConfig(model="m")):
                raise RuntimeError("boom")
        assert get_llm_config() is cached


class TestKeys:
    def test_a_subclass_answers_for_its_base(self) -> None:
        derived = _DerivedLLMConfig(model="derived")

        with override_config(derived):
            assert get_llm_config() is derived

    def test_the_exact_class_wins_over_a_subclass_for_the_base(self) -> None:
        base = LLMConfig(model="base")
        derived = _DerivedLLMConfig(model="derived")

        for args in ((base, derived), (derived, base)):
            with override_config(*args):
                assert get_llm_config() is base
                assert active_override(_DerivedLLMConfig) is derived

    def test_two_instances_of_one_class_are_refused(self) -> None:
        with pytest.raises(ValueError, match="two LLMConfig"):
            with override_config(LLMConfig(), LLMConfig()):
                pass

    def test_a_non_settings_object_is_refused(self) -> None:
        with pytest.raises(TypeError, match="settings objects"):
            with override_config({"model": "x"}):
                pass


class TestConcurrency:
    async def test_tasks_inherit_the_scope_they_were_created_in(self) -> None:
        injected = LLMConfig(model="task")

        async def read() -> LLMConfig:
            await asyncio.sleep(0)
            return get_llm_config()

        with override_config(injected):
            task = asyncio.create_task(read())
        assert await task is injected

    async def test_concurrent_scopes_do_not_leak_into_each_other(self) -> None:
        async def run(model: str) -> str:
            with override_config(LLMConfig(model=model)):
                await asyncio.sleep(0)
                return get_llm_config().model

        assert await asyncio.gather(run("a"), run("b")) == ["a", "b"]


class TestCachedAccessors:
    def test_an_lru_cached_accessor_honours_the_override(self) -> None:
        injected = MemoryRuntimeConfig()

        with override_config(injected):
            assert get_memory_runtime_config() is injected
        assert get_memory_runtime_config() is not injected

    def test_cache_clear_stays_reachable(self) -> None:
        cache_clear = get_memory_runtime_config.__dict__["cache_clear"]
        cache_clear()
        assert get_memory_runtime_config() is get_memory_runtime_config()


def _settings_accessors() -> list[tuple[str, typing.Any, type]]:
    """Every module-level ``get_*`` in baselith.core.config returning a model."""
    found = []
    modules = [config_pkg] + [
        importlib.import_module(f"{config_pkg.__name__}.{info.name}")
        for info in pkgutil.iter_modules(config_pkg.__path__)
    ]
    for module in modules:
        for name, func in vars(module).items():
            if not name.startswith("get_") or not inspect.isfunction(func):
                continue
            if func.__module__ != module.__name__:
                continue  # re-exported from another module, checked there
            wrapped = inspect.unwrap(func)
            returns = typing.get_type_hints(wrapped).get("return")
            if inspect.isclass(returns) and issubclass(returns, BaseModel):
                found.append((f"{module.__name__}.{name}", func, returns))
    return found


def test_every_settings_accessor_honours_overrides() -> None:
    """A new ``get_*_config`` that skips ``@overridable`` would silently ignore
    ``override_config``; this keeps the guarantee total."""
    accessors = _settings_accessors()
    assert len(accessors) >= 40

    missing = [
        qualified
        for qualified, func, returns in accessors
        if func.__dict__.get("__baselith_overridable__") is not returns
    ]
    assert missing == [], f"decorate with @overridable(<Config>): {missing}"
