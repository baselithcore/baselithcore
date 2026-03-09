"""
Chat Service Factory.

Provides centralized functions and orchestrators for instantiating the ChatService
and its dependencies, supporting both programmatic and configuration-driven setup.
"""

from __future__ import annotations

import json
from importlib import import_module
from pathlib import Path
from typing import Callable, Optional

from core.config import get_chat_config
from core.chat.dependencies import (
    ChatDependencies,
    ChatDependencyConfig,
    create_default_dependencies,
)
from core.chat.service import ChatService


def chat_service_factory(
    dependency_config: Optional[ChatDependencyConfig] = None,
    plugin_registry=None,
) -> ChatService:
    """
    Factory function to create a ChatService instance.

    Args:
        dependency_config: Optional configuration for dependencies
        plugin_registry: Optional plugin registry for dynamic agent loading

    Returns:
        Configured ChatService instance
    """
    dependencies = create_default_dependencies(dependency_config)
    return ChatService(dependencies=dependencies, plugin_registry=plugin_registry)


def create_chat_service_from_config(
    config: Optional[ChatDependencyConfig] = None,
    *,
    dependencies_factory: Optional[
        Callable[[Optional[ChatDependencyConfig]], ChatDependencies]
    ] = None,
) -> ChatService:
    """
    Create a ChatService instance using a specific configuration.

    Args:
        config: Dependency configuration overrides.
        dependencies_factory: Optional custom factory for building ChatDependencies.

    Returns:
        A fully initialized ChatService.
    """
    dependencies_builder = dependencies_factory or create_default_dependencies
    dependencies = _invoke_dependencies_factory(dependencies_builder, config)
    return ChatService(dependencies=dependencies)


def load_chat_dependency_config(path: Path) -> ChatDependencyConfig:
    """
    Load chat dependency configuration from a JSON file.

    Args:
        path: Path to the JSON configuration file.

    Returns:
        Parsed ChatDependencyConfig object.

    Raises:
        ValueError: If the file is not a valid JSON object.
    """
    raw = path.read_text(encoding="utf-8")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError(f"The configuration file '{path}' must contain a JSON object.")
    return ChatDependencyConfig.from_mapping(payload)


def resolve_chat_service(
    *,
    factory_path: Optional[str] = None,
    config_path: Optional[Path] = None,
) -> ChatService:
    """
    Resolve and instantiate a ChatService based on paths or environment settings.

    Args:
        factory_path: Dotted import path to a factory function.
        config_path: Path to a JSON configuration file.

    Returns:
        The resolved ChatService instance.
    """
    config = _load_config_from_path(config_path)

    factory_callable = _load_factory(factory_path)
    return _invoke_service_factory(factory_callable, config)


def _load_config_from_path(
    config_path: Optional[Path],
) -> Optional[ChatDependencyConfig]:
    path = config_path
    if path is None:
        env_value = get_chat_config().service_config_file
        if env_value:
            path = Path(env_value).expanduser()
    if path is None:
        return None
    if not path.exists():
        raise FileNotFoundError(f"Configuration file '{path}' not found.")
    return load_chat_dependency_config(path)


def _load_factory(factory_path: Optional[str]) -> Callable[..., ChatService]:
    path = factory_path or get_chat_config().service_factory
    if not path:
        return create_chat_service_from_config
    module_name, attr_name = _split_import_path(path)
    module = import_module(module_name)
    candidate = getattr(module, attr_name)
    if not callable(candidate):
        raise TypeError(
            f"The specified factory '{path}' is not callable (got {type(candidate)!r})."
        )
    return candidate


def _split_import_path(dotted_path: str) -> tuple[str, str]:
    if ":" in dotted_path:
        module_name, attr_name = dotted_path.rsplit(":", 1)
    else:
        module_name, attr_name = dotted_path.rsplit(".", 1)
    return module_name, attr_name


def _invoke_service_factory(
    factory: Callable[..., ChatService],
    config: Optional[ChatDependencyConfig],
) -> ChatService:
    try:
        if config is None:
            return factory()
        return factory(config=config)
    except TypeError as first_exc:
        try:
            if config is None:
                return factory(config=None)
            return factory(config)
        except TypeError as second_exc:
            if config is not None:
                raise TypeError(
                    "The ChatService factory must optionally accept a 'config' argument."
                ) from second_exc
            raise TypeError(
                "The ChatService factory must be callable without arguments."
            ) from first_exc


def _invoke_dependencies_factory(
    factory: Callable[[Optional[ChatDependencyConfig]], ChatDependencies],
    config: Optional[ChatDependencyConfig],
) -> ChatDependencies:
    try:
        return factory(config=config)  # type: ignore[call-arg]
    except TypeError:
        if config is None:
            return factory(None)
        return factory(config)


__all__ = [
    "create_chat_service_from_config",
    "load_chat_dependency_config",
    "resolve_chat_service",
]
