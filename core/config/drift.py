"""Detect environment variables that look like misspelled settings.

Every settings class is declared with ``extra="ignore"``, which is what lets one
process carry variables meant for another. The cost is silence: ``CORE_LOG_LEVL``
is accepted, ignored, and the operator sees the default with no hint why.

The check reports *suspected typos* rather than unknown variables. Precision is
what makes it usable: plenty of legitimate variables are read through
``os.getenv`` instead of a settings field, and flagging those would train
everyone to ignore the warning. A name close to a real setting — but not equal
to one — is almost always a mistake.

Names another component owns are exempt: a plugin's ``.env`` exports keys in its
own namespace, and a plugin may write engine keys into the environment itself.
Those are one prefix away from a core setting by construction, and reporting
them asks the operator to fix something that is not broken. Whoever writes such
a name declares it with :func:`register_owned_env`.
"""

from __future__ import annotations

import difflib
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from pydantic.aliases import AliasChoices, AliasPath
from pydantic_settings import BaseSettings

#: Families whose suffix is chosen by the caller at runtime, so no declared name
#: exists to compare against: ``BASELITH_FLAG_<FLAG>`` and
#: ``BASELITH_PROMPT_VARIANTS_<PROMPT>``.
DYNAMIC_PREFIXES: tuple[str, ...] = ("BASELITH_FLAG_", "BASELITH_PROMPT_VARIANTS_")

#: Similarity above which two names are considered a typo of one another. 0.86
#: catches a dropped or transposed character in a realistic name while leaving
#: genuinely different names (``CORE_DATA_DIR`` vs ``CORE_DOCUMENTS_DIR``) apart.
SIMILARITY_CUTOFF = 0.86

#: Upper-case names declared through :func:`register_owned_env`.
_OWNED_ENV: set[str] = set()


def register_owned_env(*names: str) -> None:
    """Declare environment variables that belong to a component, not the core.

    Call it for every name a plugin ``.env`` exports or a plugin writes into
    ``os.environ`` itself. :func:`suspected_typos` then never reports them,
    however close they are to a core setting.

    Args:
        *names: Variable names, any case; empty names are ignored.
    """
    _OWNED_ENV.update(name.upper() for name in names if name)


@dataclass(frozen=True, slots=True)
class EnvSuspect:
    """An environment variable that resembles a setting without being one."""

    name: str
    suggestion: str

    def __str__(self) -> str:
        return f"{self.name} is not a setting — did you mean {self.suggestion}?"


def _field_names(model: type[BaseSettings]) -> Iterable[str]:
    prefix = str(model.model_config.get("env_prefix") or "")
    for name, field in model.model_fields.items():
        declared = False
        for alias in (field.validation_alias, field.alias):
            if isinstance(alias, str):
                yield alias.upper()
                declared = True
            elif isinstance(alias, AliasChoices):
                for choice in alias.choices:
                    if isinstance(choice, str):
                        yield choice.upper()
                        declared = True
            elif isinstance(alias, AliasPath):  # pragma: no cover - unused today
                continue
        if not declared:
            yield f"{prefix}{name}".upper()


def _subclasses(model: type[BaseSettings]) -> Iterable[type[BaseSettings]]:
    for subclass in model.__subclasses__():
        yield subclass
        yield from _subclasses(subclass)


def known_setting_names() -> frozenset[str]:
    """Every environment name bound by a settings class loaded in this process.

    Derived from the live class tree rather than a generated list, so a plugin
    that declares its own settings is covered the moment it is imported.
    """
    return frozenset(
        name for model in _subclasses(BaseSettings) for name in _field_names(model)
    )


def suspected_typos(
    environ: Mapping[str, str] | None = None,
    *,
    cutoff: float = SIMILARITY_CUTOFF,
) -> list[EnvSuspect]:
    """Environment entries that closely resemble a setting they do not match."""
    known = known_setting_names()
    if not known:  # pragma: no cover - no settings class imported yet
        return []
    candidates = sorted(known)
    suspects: list[EnvSuspect] = []
    for name in sorted(environ if environ is not None else os.environ):
        upper = name.upper()
        if upper in known or upper in _OWNED_ENV or upper.startswith(DYNAMIC_PREFIXES):
            continue
        matches = difflib.get_close_matches(upper, candidates, n=1, cutoff=cutoff)
        if matches:
            suspects.append(EnvSuspect(name=name, suggestion=matches[0]))
    return suspects


def warn_on_suspected_typos() -> list[EnvSuspect]:
    """Log a warning per suspect at startup and return them.

    Deliberately never raises: a false positive must not be able to stop a
    deployment, and a real typo is already only costing a default.
    """
    import logging

    suspects = suspected_typos()
    logger = logging.getLogger(__name__)
    for suspect in suspects:
        logger.warning(
            "Environment variable %s matches no setting; closest is %s",
            suspect.name,
            suspect.suggestion,
        )
    return suspects


__all__ = [
    "DYNAMIC_PREFIXES",
    "SIMILARITY_CUTOFF",
    "EnvSuspect",
    "known_setting_names",
    "register_owned_env",
    "suspected_typos",
    "warn_on_suspected_typos",
]
