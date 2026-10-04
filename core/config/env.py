"""Project environment-file resolution.

Domain-agnostic helper that locates the ``.env`` files the settings classes
read, so that core and plugin ``BaseSettings`` classes see the same overrides
regardless of how the framework was installed.

Two files are candidates, loaded in this order (first value wins):

1. ``.env`` in the **current working directory** — the project the operator
   is running. Under ``pip install baselith-core`` this is the only one that
   can exist: the package lives in ``site-packages``, so the second candidate
   below points inside the virtualenv, where no ``.env`` ever is. Before this
   candidate existed, the ``.env`` that ``baselith init`` writes was never
   read by an installed framework, and a freshly scaffolded project refused
   to start for want of the ``SECRET_KEY`` sitting in its own ``.env``.
2. ``PROJECT_ENV_FILE`` — the ``.env`` at the root of the checkout this
   ``core`` package was imported from (``core/config/env.py`` ->
   ``parents[2]``). For a contributor running from the checkout root both
   candidates are the same file, which is then read once.

``PROJECT_ENV_FILE`` stays exported and is safe to pass to
``pydantic_settings`` ``env_file`` even when the file does not exist (Pydantic
treats a missing env file as "no overrides").
"""

import logging
import os
import stat
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# core/config/env.py -> parents[0]=config, parents[1]=core, parents[2]=repo root
PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]

#: Absolute path to the repository-root ``.env`` file.
PROJECT_ENV_FILE: Path = PROJECT_ROOT / ".env"

_env_loaded = False


def env_file_candidates() -> list[Path]:
    """The ``.env`` files :func:`load_project_env` reads, first value wins.

    Returns:
        The working directory's ``.env``, then :data:`PROJECT_ENV_FILE` when
        it is a different file. Paths may not exist.
    """
    candidates = [Path.cwd() / ".env"]
    if PROJECT_ENV_FILE.resolve() != candidates[0].resolve():
        candidates.append(PROJECT_ENV_FILE)
    return candidates


def load_project_env() -> None:
    """Load the project ``.env`` files into ``os.environ`` exactly once.

    Core config classes used to each declare ``env_file=".env"``, so every
    ``BaseSettings`` instantiation re-read and re-parsed the same file
    (20+ parses, 200ms+ at startup). The package now loads it here once —
    with ``override=False`` so real environment variables keep precedence,
    matching pydantic-settings' env-over-dotenv ordering. Because no file
    overrides, the first candidate of :func:`env_file_candidates` that sets a
    variable wins over the later ones. Idempotent.
    """
    global _env_loaded
    if _env_loaded:
        return
    for candidate in env_file_candidates():
        if is_trusted_env_file(candidate):
            load_dotenv(candidate, override=False)
    _env_loaded = True


def is_trusted_env_file(path: Path) -> bool:
    """Whether ``path`` may feed settings into this process.

    The working directory is a trusted input (``./plugins`` and
    ``configs/plugins.yaml`` are read from it too), but a ``.env`` another
    local user can write is not: it could point the plugin path, the database
    or the LLM endpoint elsewhere. On POSIX the file must belong to the
    process's effective user (or root, as in container images) and must not
    be group- or world-writable — the same fail-closed rule as the plugin
    trust store. A missing file is trivially fine; a refused one is logged.

    Args:
        path: A candidate from :func:`env_file_candidates`.

    Returns:
        ``True`` when the file is absent or safe to load.
    """
    try:
        info = path.stat()
    except FileNotFoundError:
        return True
    except OSError as exc:
        logger.warning("Not loading %s: cannot stat it (%s)", path, exc)
        return False
    if os.name != "posix":
        return True
    if info.st_uid not in (os.geteuid(), 0):
        logger.warning(
            "Not loading %s: owned by uid %d, not by this user (uid %d) or root",
            path,
            info.st_uid,
            os.geteuid(),
        )
        return False
    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        logger.warning(
            "Not loading %s: it is group- or world-writable (chmod 600 it)", path
        )
        return False
    return True


# Loaded at import time on purpose: core.config.__init__ imports this module
# first, guaranteeing the environment is populated before any BaseSettings
# class (some of which instantiate at import, e.g. evaluation_config).
load_project_env()

__all__ = [
    "PROJECT_ENV_FILE",
    "PROJECT_ROOT",
    "env_file_candidates",
    "is_trusted_env_file",
    "load_project_env",
]
