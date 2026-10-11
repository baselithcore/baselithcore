"""Aliased settings fields accept their Python name in code, and only there.

A field with ``validation_alias`` binds only its alias, so a keyword named
after the field used to be dropped and the instance fell back to the
environment. ``populate_by_name`` is not the fix: pydantic-settings would also
read ``<env_prefix><field_name>`` from the environment, which for the classes
whose aliases are complete names means bare variables such as ``ENABLED``.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil

import pytest
from pydantic import SecretStr
from pydantic_settings import BaseSettings

import baselith.core.config as config_pkg
from baselith.core.config._aliased import AliasedSettings, by_alias
from baselith.core.config.multimodal import VoiceConfig
from baselith.core.config.webhooks import WebhookConfig


def _settings_classes() -> list[type[BaseSettings]]:
    found: list[type[BaseSettings]] = []
    for info in pkgutil.iter_modules(config_pkg.__path__):
        module = importlib.import_module(f"{config_pkg.__name__}.{info.name}")
        for obj in vars(module).values():
            if (
                inspect.isclass(obj)
                and issubclass(obj, BaseSettings)
                and obj.__module__ == module.__name__
            ):
                found.append(obj)
    return found


def test_a_keyword_named_after_an_aliased_field_is_applied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for var in ("VOICE_OPENAI_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    config = VoiceConfig(_env_file=None, openai_api_key=SecretStr("sk-by-name"))
    assert config.openai_api_key is not None
    assert config.openai_api_key.get_secret_value() == "sk-by-name"


def test_the_keyword_beats_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEBHOOK_ALLOW_INTERNAL", "false")
    assert WebhookConfig(allow_internal=True).allow_internal is True


def test_the_field_name_never_binds_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WEBHOOK_ALLOW_INTERNAL", raising=False)
    monkeypatch.setenv("ALLOW_INTERNAL", "true")
    assert WebhookConfig().allow_internal is False


def test_the_alias_still_binds_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WEBHOOK_ALLOW_INTERNAL", "true")
    assert WebhookConfig().allow_internal is True


def test_a_value_given_under_the_alias_wins_over_the_name() -> None:
    values = by_alias(
        WebhookConfig, {"allow_internal": True, "WEBHOOK_ALLOW_INTERNAL": False}
    )
    assert values == {"WEBHOOK_ALLOW_INTERNAL": False}


def test_every_aliased_settings_class_accepts_names() -> None:
    """A new aliased class must not silently drop keywords again."""
    offenders = [
        cls.__name__
        for cls in _settings_classes()
        if any(f.validation_alias or f.alias for f in cls.model_fields.values())
        and not issubclass(cls, AliasedSettings)
        and not cls.model_config.get("populate_by_name")
    ]
    assert offenders == []
