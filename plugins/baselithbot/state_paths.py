"""Where Baselithbot keeps its plugin-local state.

The state directory holds the secret-store master key (``.secret_key``), the
encrypted provider/channel stores, ``replay.sqlite``, ``workspaces.json`` and
the other per-instance files. It must never live inside the installed
package: ``site-packages`` is shared, may be read-only, and is replaced on
every upgrade — which would silently rotate the master key and orphan every
encrypted secret.

Resolution order:

1. ``BASELITHBOT_STATE_DIR`` — an explicit directory, used as given.
2. A legacy ``plugins/baselithbot/.state`` that already exists — kept so an
   existing install does not lose its key and stores, with a deprecation
   warning asking the operator to move it.
3. The per-user data directory: ``$XDG_DATA_HOME/baselith/baselithbot``,
   falling back to ``~/.local/share/baselith/baselithbot``.

The directory is created owner-only (``0700``).
"""

from __future__ import annotations

import os
import warnings
from pathlib import Path

from core.observability.logging import get_logger

logger = get_logger(__name__)

STATE_DIR_ENV = "BASELITHBOT_STATE_DIR"
"""Environment variable naming the state directory explicitly."""

LEGACY_STATE_DIR = Path(__file__).resolve().parent / ".state"
"""The former default, inside the plugin package (kept when it exists)."""


def user_state_dir() -> Path:
    """The per-user default: ``$XDG_DATA_HOME/baselith/baselithbot``."""
    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "baselith" / "baselithbot"


def resolve_state_dir() -> Path:
    """Resolve and create the Baselithbot state directory.

    Returns:
        The directory, created with mode ``0700`` when this call creates it.
    """
    override = os.environ.get(STATE_DIR_ENV, "").strip()
    if override:
        path = Path(override).expanduser()
    elif LEGACY_STATE_DIR.is_dir():
        message = (
            f"Baselithbot state found inside the plugin package at "
            f"{LEGACY_STATE_DIR}; it is still used, but this location is "
            f"deprecated. Move it to {user_state_dir()} or point "
            f"{STATE_DIR_ENV} at it."
        )
        warnings.warn(message, DeprecationWarning, stacklevel=2)
        logger.warning("baselithbot_legacy_state_dir", path=str(LEGACY_STATE_DIR))
        return LEGACY_STATE_DIR
    else:
        path = user_state_dir()
    if not path.is_dir():
        path.mkdir(parents=True, mode=0o700, exist_ok=True)
        # mkdir's mode is masked by the umask; state holds key material.
        path.chmod(0o700)
    return path


__all__ = [
    "LEGACY_STATE_DIR",
    "STATE_DIR_ENV",
    "resolve_state_dir",
    "user_state_dir",
]
