"""Plugin-local ``.env`` loading — operator config scoped to one plugin.

Convention: plugin-specific environment keys (namespaced ``<PLUGIN>_*``) live
in ``plugins/<name>/.env``, never in the repo-root ``.env``, which is reserved
for framework/core-level configuration. This keeps the host env clean, avoids
cross-plugin key confusion, and lets each plugin ship its own documented
operator config next to its code (the file is gitignored like every ``.env``).

Loading is additive and **scoped by construction**:

- Only keys in the plugin's own namespace are loaded — by default those
  prefixed ``<DIRNAME>_`` (e.g. ``BASELITHBOT_*`` for ``plugins/baselithbot``),
  or an explicit ``allowed_prefixes``. A plugin ``.env`` therefore cannot flip a
  framework/core key it does not own (``MCP_HTTP_REQUIRE_AUTH``,
  ``BASELITH_SKIP_INTEGRITY_CHECK``, ``ALLOW_ORIGINS``, …); out-of-namespace
  keys are refused, not silently injected into the global process env.
- Existing process env always wins (``os.environ.setdefault`` semantics).
- A **symlinked** ``.env`` is refused — it must not point outside the plugin at
  host secrets (``.env -> /etc/…`` or ``-> ../../.env``); the file must resolve
  to a real file directly inside the plugin directory.
- A missing file or an absent ``python-dotenv`` is a no-op; nothing raises.

Note: ``.env`` is **not** covered by plugin integrity hashing (only ``.py`` and
``.pyi`` are), so treat it as operator-supplied config, never as trusted code.

Call it at plugin module import (or activation) time, before the plugin reads
its configuration::

    from core.plugins.env import load_plugin_dotenv

    load_plugin_dotenv(Path(__file__).parent)
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from core.observability.logging import get_logger

logger = get_logger(__name__)


def _default_prefix(plugin_dir: Path) -> str:
    """Derive the ``<PLUGIN>_`` env-key namespace from the plugin directory name."""
    slug = re.sub(r"[^A-Za-z0-9]+", "_", plugin_dir.name).strip("_").upper()
    return f"{slug}_" if slug else ""


def load_plugin_dotenv(
    plugin_dir: str | Path,
    *,
    allowed_prefixes: tuple[str, ...] | None = None,
) -> bool:
    """Load ``<plugin_dir>/.env`` into the process env, scoped to the plugin.

    Args:
        plugin_dir: The plugin's directory (typically ``Path(__file__).parent``
            from the plugin's ``plugin.py``).
        allowed_prefixes: Env-key prefixes the plugin owns. Defaults to a single
            prefix derived from the directory name (``document_sources`` →
            ``DOCUMENT_SOURCES_``). Pass explicit prefixes when the plugin's keys
            use a namespace that differs from its directory name.

    Returns:
        True when a ``.env`` was found and parsed, False otherwise. Never raises.
    """
    plugin_path = Path(plugin_dir)
    env_file = plugin_path / ".env"
    try:
        if env_file.is_symlink():
            logger.warning("Refusing symlinked plugin .env: %s", env_file)
            return False
        if not env_file.is_file():
            return False
        # Containment: the resolved file must sit directly in the plugin dir —
        # a defence-in-depth check on top of the symlink refusal above.
        if env_file.resolve().parent != plugin_path.resolve():
            logger.warning("Plugin .env escapes its plugin dir: %s", env_file)
            return False

        prefixes = tuple(
            p for p in (allowed_prefixes or (_default_prefix(plugin_path),)) if p
        )
        if not prefixes:
            logger.warning(
                "No resolvable namespace prefix for %s; skipping .env", plugin_path
            )
            return False

        from dotenv import dotenv_values

        values = dotenv_values(env_file)
        loaded = 0
        skipped: list[str] = []
        for key, value in values.items():
            if value is None:
                continue
            if not key.startswith(prefixes):
                skipped.append(key)
                continue
            if key not in os.environ:
                os.environ[key] = value
                loaded += 1
        if skipped:
            logger.warning(
                "Plugin .env %s: refused %d out-of-namespace key(s) "
                "(allowed prefixes: %s): %s",
                env_file,
                len(skipped),
                ", ".join(prefixes),
                ", ".join(sorted(skipped)),
            )
        logger.debug(
            "Loaded %d in-namespace key(s) from plugin .env: %s", loaded, env_file
        )
        return True
    except Exception:
        logger.warning("Plugin .env load failed: %s", env_file, exc_info=True)
        return False


__all__ = ["load_plugin_dotenv"]
