"""Plugin-local ``.env`` loading — operator config scoped to one plugin.

Convention: plugin-specific environment keys (namespaced ``<PLUGIN>_*``) live
in ``plugins/<name>/.env``, never in the repo-root ``.env``, which is reserved
for framework/core-level configuration. This keeps the host env clean, avoids
cross-plugin key confusion, and lets each plugin ship its own documented
operator config next to its code (the file is gitignored like every ``.env``).

Loading is additive and safe by construction: existing process env always wins
(``override=False``), a missing file is a no-op, and neither a malformed file
nor an absent ``python-dotenv`` can break host boot. Call it at plugin module
import (or activation) time, before the plugin reads its configuration:

    from core.plugins.env import load_plugin_dotenv

    load_plugin_dotenv(Path(__file__).parent)
"""

from __future__ import annotations

from pathlib import Path

from core.observability.logging import get_logger

logger = get_logger(__name__)


def load_plugin_dotenv(plugin_dir: str | Path) -> bool:
    """Load ``<plugin_dir>/.env`` into the process env (existing keys win).

    Args:
        plugin_dir: The plugin's directory (typically ``Path(__file__).parent``
            from the plugin's ``plugin.py``).

    Returns:
        True when a file was found and loaded, False otherwise. Never raises.
    """
    env_file = Path(plugin_dir) / ".env"
    if not env_file.is_file():
        return False
    try:
        from dotenv import load_dotenv

        load_dotenv(env_file, override=False)
        logger.debug("Loaded plugin .env: %s", env_file)
        return True
    except Exception:
        logger.warning("Plugin .env load failed: %s", env_file, exc_info=True)
        return False


__all__ = ["load_plugin_dotenv"]
