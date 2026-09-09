"""Introspection of the framework's configuration surface.

The settings live in ~35 ``BaseSettings`` classes under ``core/config/`` (plus a
handful in plugins); the operator-facing view of them lives in ``.env.example``
and in the docs. Nothing kept those three in sync, so this package reads the
classes and lets ``scripts/check_config_surface.py`` generate the reference page
and flag ``.env.example`` entries that bind to nothing.
"""

from scripts.config_surface.introspect import (
    Setting,
    env_example_entries,
    env_literals,
    iter_settings,
)
from scripts.config_surface.render import render_reference

__all__ = [
    "Setting",
    "env_example_entries",
    "env_literals",
    "iter_settings",
    "render_reference",
]
