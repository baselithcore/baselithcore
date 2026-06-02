"""Framework-wide exception hierarchy.

A single, domain-agnostic root (:class:`BaselithError`) plus the small set of
infrastructure error families the core and plugin framework raise. Plugins and
domain code should subclass the family that best fits rather than raising bare
``Exception``/``RuntimeError`` — this gives callers (and observability) a stable
way to distinguish *plugin* failures from *registry* failures from everything
else.

The hierarchy is intentionally shallow and generic; domain-specific error types
belong in the plugin that owns the domain, subclassing the relevant family here.
"""

from __future__ import annotations


class BaselithError(Exception):
    """Root of all BaselithCore-raised errors."""


# ---------------------------------------------------------------------------
# Plugin framework
# ---------------------------------------------------------------------------


class PluginError(BaselithError):
    """Base for errors originating in the plugin framework or a plugin."""


class PluginInitError(PluginError):
    """A plugin failed during ``initialize()``."""


class PluginConfigError(PluginError):
    """A plugin's configuration is invalid (e.g. failed its JSON Schema)."""


class PluginIntegrityError(PluginError):
    """A plugin failed signing/integrity verification."""


class PluginDependencyError(PluginError):
    """A plugin's core-version bounds or plugin dependencies are unsatisfied."""


# ---------------------------------------------------------------------------
# Registries
# ---------------------------------------------------------------------------


class RegistryError(BaselithError):
    """Base for registry lookup/registration failures."""


class DuplicateRegistrationError(RegistryError):
    """An item was registered under a name already in use (overwrite disabled)."""


class ItemNotFoundError(RegistryError):
    """A lookup was made for a name that is not registered."""


__all__ = [
    "BaselithError",
    "PluginError",
    "PluginInitError",
    "PluginConfigError",
    "PluginIntegrityError",
    "PluginDependencyError",
    "RegistryError",
    "DuplicateRegistrationError",
    "ItemNotFoundError",
]
