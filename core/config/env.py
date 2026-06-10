"""Project environment-file resolution.

Domain-agnostic helper that locates the repository-root ``.env`` file so that
plugin ``BaseSettings`` classes can load environment overrides from a single,
predictable location regardless of the process working directory.

The repository root is resolved relative to this module
(``core/config/env.py`` -> ``parents[2]`` is the repo root). The resolved path
is exported as ``PROJECT_ENV_FILE`` and is safe to pass to
``pydantic_settings`` ``env_file`` even when the file does not exist (Pydantic
treats a missing env file as "no overrides").
"""

from pathlib import Path

from dotenv import load_dotenv

# core/config/env.py -> parents[0]=config, parents[1]=core, parents[2]=repo root
PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]

#: Absolute path to the repository-root ``.env`` file.
PROJECT_ENV_FILE: Path = PROJECT_ROOT / ".env"

_env_loaded = False


def load_project_env() -> None:
    """Load the repository ``.env`` into ``os.environ`` exactly once.

    Core config classes used to each declare ``env_file=".env"``, so every
    ``BaseSettings`` instantiation re-read and re-parsed the same file
    (20+ parses, 200ms+ at startup). The package now loads it here once —
    with ``override=False`` so real environment variables keep precedence,
    matching pydantic-settings' env-over-dotenv ordering. Idempotent.
    """
    global _env_loaded
    if _env_loaded:
        return
    load_dotenv(PROJECT_ENV_FILE, override=False)
    _env_loaded = True


# Loaded at import time on purpose: core.config.__init__ imports this module
# first, guaranteeing the environment is populated before any BaseSettings
# class (some of which instantiate at import, e.g. evaluation_config).
load_project_env()

__all__ = ["PROJECT_ROOT", "PROJECT_ENV_FILE", "load_project_env"]
