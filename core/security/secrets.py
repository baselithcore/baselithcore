"""Pluggable secret resolution.

Decouples *where* a secret comes from (process environment, mounted secret
files, or — via a registered backend — an external manager such as HashiCorp
Vault or a cloud KMS) from *how* the application consumes it. Every backend
returns values wrapped in :class:`pydantic.SecretStr` so credentials never leak
through ``repr()``, logs, or Sentry frames.

The default backend is :class:`EnvSecretsProvider`, which preserves the
framework's existing environment-variable behaviour exactly — selecting a
different backend is strictly opt-in.

File backend & the ``_FILE`` convention
---------------------------------------
:class:`FileSecretsProvider` reads each secret from its own file under a
directory (the Docker/Kubernetes secrets pattern, e.g. ``/run/secrets``). It
also honours the widely used ``<NAME>_FILE`` indirection: if ``DB_PASSWORD`` is
unset but ``DB_PASSWORD_FILE`` points at a path, the file's contents are used.
This keeps plaintext secrets out of the environment and image layers.

Registering external backends
-----------------------------
Heavy or environment-specific providers (Vault, AWS/GCP/Azure) are kept out of
``core`` to honour the Sacred Core rule. Register them at startup::

    from core.security.secrets import register_secrets_provider
    register_secrets_provider("vault", lambda: MyVaultProvider(...))

and select via ``SECRETS_BACKEND=vault``.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import SecretStr

logger = logging.getLogger(__name__)


@runtime_checkable
class SecretsProvider(Protocol):
    """Resolves named secrets to :class:`SecretStr` values."""

    def get_secret(self, name: str) -> SecretStr | None:
        """Return the secret for ``name``, or ``None`` if absent."""
        ...


class EnvSecretsProvider:
    """Resolve secrets from process environment variables.

    This is the default provider and is behaviourally identical to reading
    ``os.environ`` directly, ensuring no change to existing deployments.
    """

    def get_secret(self, name: str) -> SecretStr | None:
        """Return ``os.environ[name]`` wrapped in :class:`SecretStr`, if set."""
        value = os.environ.get(name)
        return SecretStr(value) if value is not None else None


class FileSecretsProvider:
    """Resolve secrets from per-secret files (Docker/Kubernetes secrets).

    Lookup order for a name ``N``:

    1. ``<secrets_dir>/N`` (exact), then ``<secrets_dir>/n`` (lower-cased).
    2. The path in environment variable ``N_FILE`` (the ``_FILE`` convention).
    3. If ``fallback_env`` is set, the plain environment variable ``N``.

    File contents are read as UTF-8 and stripped of a single trailing newline.
    """

    def __init__(
        self,
        secrets_dir: Path | str | None = None,
        *,
        fallback_env: bool = True,
    ) -> None:
        self._dir = Path(secrets_dir) if secrets_dir else None
        self._fallback_env = fallback_env

    def get_secret(self, name: str) -> SecretStr | None:
        """Resolve ``name`` from the secrets directory, ``_FILE`` var, or env."""
        if self._dir is not None:
            for candidate in (self._dir / name, self._dir / name.lower()):
                value = self._read(candidate)
                if value is not None:
                    return SecretStr(value)
        file_path = os.environ.get(f"{name}_FILE")
        if file_path:
            value = self._read(Path(file_path))
            if value is not None:
                return SecretStr(value)
        if self._fallback_env:
            env_value = os.environ.get(name)
            if env_value is not None:
                return SecretStr(env_value)
        return None

    @staticmethod
    def _read(path: Path) -> str | None:
        """Read and trim a secret file, returning ``None`` if unreadable."""
        try:
            if not path.is_file():
                return None
            # Strip exactly one trailing newline (common when files are echoed).
            return path.read_text(encoding="utf-8").rstrip("\n")
        except OSError as exc:
            logger.warning("Unable to read secret file %s: %s", path, exc)
            return None


#: Registry of named provider factories for externally-supplied backends.
_PROVIDER_FACTORIES: dict[str, Callable[[], SecretsProvider]] = {}

#: Process-wide cached provider, built lazily on first :func:`get_secrets_provider`.
_provider: SecretsProvider | None = None


def register_secrets_provider(
    name: str, factory: Callable[[], SecretsProvider]
) -> None:
    """Register a secrets-provider factory under ``name`` (e.g. ``"vault"``).

    Call during application startup, before :func:`get_secrets_provider`. The
    factory is invoked lazily the first time the matching backend is selected.

    Args:
        name: Backend identifier matched against ``SECRETS_BACKEND``.
        factory: Zero-argument callable returning a :class:`SecretsProvider`.
    """
    _PROVIDER_FACTORIES[name.lower()] = factory
    logger.debug("Registered secrets provider backend %r", name)


def _build_provider(backend: str, secrets_dir: str | None) -> SecretsProvider:
    """Construct the provider for ``backend`` (built-ins + registered)."""
    backend = backend.lower()
    if backend == "env":
        return EnvSecretsProvider()
    if backend == "file":
        return FileSecretsProvider(secrets_dir)
    factory = _PROVIDER_FACTORIES.get(backend)
    if factory is not None:
        return factory()
    raise ValueError(
        f"Unknown secrets backend {backend!r}. Built-ins: 'env', 'file'. "
        f"Registered: {sorted(_PROVIDER_FACTORIES)}."
    )


def get_secrets_provider() -> SecretsProvider:
    """Return the process-wide secrets provider, building it on first use.

    Backend selection reads ``SECRETS_BACKEND`` (default ``env``) and, for the
    file backend, ``SECRETS_DIR``. The result is cached; call
    :func:`reset_secrets_provider` in tests to force a rebuild.
    """
    global _provider
    if _provider is None:
        backend = os.environ.get("SECRETS_BACKEND", "env")
        secrets_dir = os.environ.get("SECRETS_DIR")
        _provider = _build_provider(backend, secrets_dir)
        logger.info("Initialized secrets provider (backend=%s)", backend)
    return _provider


def reset_secrets_provider() -> None:
    """Clear the cached provider. Intended for tests only."""
    global _provider
    _provider = None


def get_secret(name: str) -> SecretStr | None:
    """Convenience accessor delegating to the active provider."""
    return get_secrets_provider().get_secret(name)


__all__ = [
    "EnvSecretsProvider",
    "FileSecretsProvider",
    "SecretsProvider",
    "get_secret",
    "get_secrets_provider",
    "register_secrets_provider",
    "reset_secrets_provider",
]
