"""Load-time admission gates for plugins.

These helpers decide whether a plugin may proceed to initialize based on its
declared version compatibility and config schema. **Both gates fail closed**: a
plugin whose declared core-version bounds, plugin dependencies or config schema
are not satisfied is skipped. The manifest's declarations are the author's
statement of what is safe to run; ignoring them and loading anyway was never a
defensible default.

The matching environment variables survive as explicit *downgrade* flags for an
operator who needs to boot a deployment while a manifest is corrected:
``BASELITH_ENFORCE_PLUGIN_COMPAT=false`` / ``BASELITH_ENFORCE_PLUGIN_CONFIG=false``
restore the old warn-only behaviour.

Kept out of ``loader.py`` to respect the 500-line module cap.
"""

from __future__ import annotations

from typing import Any

from core._version import __version__ as CORE_VERSION
from core.observability.logging import get_logger

from .config_validation import is_config_enforcement_enabled, validate_plugin_config
from .interface import Plugin
from .version import check_plugin_compatibility, is_compat_enforcement_enabled

logger = get_logger(__name__)


def is_config_gate_enforced() -> bool:
    """Whether an invalid plugin config blocks loading.

    Alias of :func:`core.plugins.config_validation.is_config_enforcement_enabled`,
    which owns the single reading of ``BASELITH_ENFORCE_PLUGIN_CONFIG``. The two
    used to answer differently for an unset environment — this one fail-closed,
    that one opt-in — which meant the same variable meant two things depending
    on which function you happened to call.

    Returns:
        True unless the environment explicitly disables enforcement.
    """
    return is_config_enforcement_enabled()


def config_gate(plugin: Plugin, config: dict[str, Any]) -> bool:
    """Validate a plugin's config against its declared JSON Schema.

    Returns True when the plugin may proceed to initialize. When validation
    fails, the plugin is skipped only if config enforcement is enabled;
    otherwise problems are logged as warnings and loading continues.
    """
    try:
        schema = plugin.get_config_schema()
    except Exception as e:  # defensive: a broken hook must not block loading
        logger.warning(f"Could not read config schema for {plugin.metadata.name}: {e}")
        return True

    problems = validate_plugin_config(schema, config)
    if not problems:
        return True

    detail = "; ".join(problems)
    if is_config_gate_enforced():
        logger.error(
            f"Skipping plugin {plugin.metadata.name}: invalid config ({detail}). "
            "Set BASELITH_ENFORCE_PLUGIN_CONFIG=false to downgrade this to a warning."
        )
        return False
    logger.warning(f"Plugin {plugin.metadata.name} config validation warning: {detail}")
    return True


def compat_gate(plugin: Plugin, available_versions: dict[str, str]) -> bool:
    """Check a plugin's core/plugin-dependency version compatibility.

    Returns True when the plugin may load. An incompatibility skips the plugin
    unless enforcement has been explicitly disabled with
    ``BASELITH_ENFORCE_PLUGIN_COMPAT=false``, in which case it is logged as a
    warning and loading continues.
    """
    md = plugin.metadata
    problems = check_plugin_compatibility(
        core_version=CORE_VERSION,
        min_core_version=md.min_core_version,
        max_core_version=md.max_core_version,
        plugin_dependencies=md.plugin_dependencies,
        available_versions=available_versions,
    )
    if not problems:
        return True

    detail = "; ".join(problems)
    if is_compat_enforcement_enabled():
        logger.error(
            f"Skipping incompatible plugin {md.name}: {detail}. "
            "Set BASELITH_ENFORCE_PLUGIN_COMPAT=false to downgrade this to a warning."
        )
        return False
    logger.warning(f"Plugin {md.name} compatibility warning: {detail}")
    return True


# Five libraries were core dependencies "for plugins" although nothing in the
# core or the official plugins imported them; they moved to the
# ``plugin-compat`` extra. A third-party plugin that imported one without
# declaring it now fails with a bare ModuleNotFoundError, which names a module
# but not the remedy — missing_dependency_hint supplies it, for those names only.

#: Import name -> distribution name, for the libraries the core stopped
#: installing by default.
MOVED_TO_PLUGIN_COMPAT: dict[str, str] = {
    "defusedxml": "defusedxml",
    "email_validator": "email-validator",
    "markdown_it": "markdown-it-py",
    "networkx": "networkx",
    "sse_starlette": "sse-starlette",
}


def missing_dependency_hint(error: BaseException) -> str:
    """Return a remedy to append to a plugin load error, or ``""``.

    Args:
        error: The exception the plugin raised while its module executed.

    Returns:
        A sentence naming the ``plugin-compat`` extra and the
        ``python_dependencies`` manifest field when ``error`` is a missing
        module the core used to install; an empty string otherwise.
    """
    if not isinstance(error, ModuleNotFoundError) or not error.name:
        return ""
    distribution = MOVED_TO_PLUGIN_COMPAT.get(error.name.split(".", 1)[0])
    if distribution is None:
        return ""
    return (
        f" — '{distribution}' is no longer installed with baselith-core. "
        f"Add it to the plugin's python_dependencies, or install "
        f"baselith-core[plugin-compat] to restore the previous set."
    )
