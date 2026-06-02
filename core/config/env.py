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

# core/config/env.py -> parents[0]=config, parents[1]=core, parents[2]=repo root
PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]

#: Absolute path to the repository-root ``.env`` file.
PROJECT_ENV_FILE: Path = PROJECT_ROOT / ".env"

__all__ = ["PROJECT_ROOT", "PROJECT_ENV_FILE"]
