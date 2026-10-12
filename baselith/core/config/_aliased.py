"""Settings whose aliased fields also accept their Python name in code.

A field declared with ``validation_alias``/``alias`` binds only that alias, so
``VoiceConfig(openai_api_key=...)`` was dropped without a word and the
instance fell back to the environment. ``populate_by_name`` would fix the
constructor but, in pydantic-settings, it also makes the environment source
read ``<env_prefix><field_name>``. For classes whose aliases are complete
variable names that means bare names such as ``ENABLED`` or
``ALLOW_INTERNAL`` start binding several unrelated settings at once.

:class:`AliasedSettings` fixes only the constructor: a keyword named after an
aliased field is renamed to the field's primary alias before validation. The
environment variables a class reads are exactly the ones it declares.
"""

from __future__ import annotations

from typing import Any

from pydantic import AliasChoices
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings


def _input_key(info: FieldInfo) -> str | None:
    """The alias a value for *info* is validated under, or ``None``."""
    alias = info.validation_alias or info.alias
    if isinstance(alias, str):
        return alias
    if isinstance(alias, AliasChoices):
        for choice in alias.choices:
            if isinstance(choice, str):
                return choice
    return None


def by_alias(cls: type[BaseSettings], values: dict[str, Any]) -> dict[str, Any]:
    """*values* with each aliased field's Python name renamed to its alias.

    A value already given under the alias wins over one given by name.
    """
    out = dict(values)
    for name, info in cls.model_fields.items():
        if name not in out:
            continue
        key = _input_key(info)
        if key is None or key == name:
            continue
        value = out.pop(name)
        out.setdefault(key, value)
    return out


class AliasedSettings(BaseSettings):
    """``BaseSettings`` whose aliased fields can be set by name in code."""

    def __init__(self, **values: Any) -> None:
        super().__init__(**by_alias(type(self), values))


__all__ = ["AliasedSettings", "by_alias"]
