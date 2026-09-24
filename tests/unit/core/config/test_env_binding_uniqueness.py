"""An environment variable means one thing, whichever settings class reads it.

Regressions this guards:

* ``CHAT_RESPONSE_CACHE_TTL`` was bound by ``AppConfig`` and, through the
  ``CHAT_`` prefix, by ``ChatConfig`` too — with separate defaults.
* ``CHAT_MEMORY_ENABLED`` meant conversation history in one class and
  long-term memory in another, with opposite defaults.
* ``CACHE_REDIS_URL`` defaulted to ``redis://redis:6379/1`` for the rate
  limiter and idempotency middleware but ``redis://localhost:6379/1`` for the
  caches, so an unset variable sent them to different hosts.

A variable may be shared on purpose (a provider API key read by several
integrations); then every concrete default must agree. A ``None`` default
defers to the class's own fallback (``TaskQueueConfig.get_redis_url``) and is
not compared.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from collections import defaultdict
from typing import Any

from pydantic import AliasChoices
from pydantic_settings import BaseSettings

import core.config


def _bindings() -> dict[str, list[tuple[str, Any, Any]]]:
    seen: dict[str, list[tuple[str, Any, Any]]] = defaultdict(list)
    for module_info in pkgutil.iter_modules(core.config.__path__):
        module = importlib.import_module(f"core.config.{module_info.name}")
        for name, cls in inspect.getmembers(module, inspect.isclass):
            if (
                not issubclass(cls, BaseSettings)
                or cls is BaseSettings
                or cls.__module__ != module.__name__
            ):
                continue
            prefix = cls.model_config.get("env_prefix") or ""
            for field_name, info in cls.model_fields.items():
                alias = info.validation_alias
                if isinstance(alias, str):
                    names = {alias}
                elif isinstance(alias, AliasChoices):
                    names = {c for c in alias.choices if isinstance(c, str)}
                elif info.alias:
                    names = {info.alias}
                else:
                    names = {prefix + field_name}
                for env_name in names:
                    seen[env_name.upper()].append(
                        (f"{name}.{field_name}", info.default, info.annotation)
                    )
    return seen


def test_shared_variables_agree_on_their_default() -> None:
    conflicts = {
        env: bindings
        for env, bindings in _bindings().items()
        if len({repr(d) for _, d, _t in bindings if d is not None}) > 1
    }
    assert conflicts == {}
