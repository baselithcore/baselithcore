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

External backends
-----------------
Heavy or environment-specific providers (Vault, AWS/GCP/Azure) are kept out of
``core`` to honour the Sacred Core rule. They arrive one of two ways, and are
selected the same way: ``SECRETS_BACKEND=<name>``.

1. **As an installed package** (the deployment path). A distribution declares
   a zero-argument factory under the ``baselith.secrets_providers`` entry-point
   group; installing it and setting the environment variable is all an
   operator does::

       [project.entry-points."baselith.secrets_providers"]
       vault = "acme_vault_secrets:VaultSecretsProvider"

   The group is consulted lazily, only when the selected backend is neither a
   built-in nor explicitly registered, so the default ``env`` path never
   touches package metadata. This works even when the first secret read
   happens during bootstrap, before any plugin has loaded.

2. **Registered in-process** (tests, embedding)::

       from core.security.secrets import register_secrets_provider
       register_secrets_provider("vault", lambda: MyVaultProvider(...))

Precedence: the built-ins ``env`` and ``file`` cannot be shadowed, an explicit
registration beats an entry point. Resolution fails closed: an unknown name,
two distributions claiming the same name, an entry point that fails to load,
or a factory returning something that is not a :class:`SecretsProvider` all
raise — never a silent fallback to ``env``, which would read credentials from
the wrong place.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pydantic import SecretStr

if TYPE_CHECKING:
    from importlib.metadata import EntryPoint

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

    File contents are read as UTF-8 and stripped of trailing newlines.
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
            # Neither the path nor the name: both are derived from
            # SECRETS_DIR, and a secrets path in a log line is exactly what an
            # auditor objects to. The errno text says what went wrong; the
            # caller already knows which secret it asked for.
            logger.warning(
                "File provider could not read the requested entry: %s",
                exc.strerror or type(exc).__name__,
            )
            return None


#: Backends implemented here. They cannot be shadowed by a registration or
#: an entry point: ``SECRETS_BACKEND=env`` must mean the environment, always.
_BUILTIN_BACKENDS: frozenset[str] = frozenset({"env", "file"})

#: Entry-point group through which installed packages contribute backends.
#: Entry-point name = backend id; object = zero-argument provider factory.
ENTRY_POINT_GROUP = "baselith.secrets_providers"

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
    A registration takes precedence over an installed entry point of the same
    name. Deployments should prefer shipping the provider as a package with a
    ``baselith.secrets_providers`` entry point, which needs no startup code.

    Args:
        name: Backend identifier matched against ``SECRETS_BACKEND``.
        factory: Zero-argument callable returning a :class:`SecretsProvider`.

    Raises:
        ValueError: If ``name`` is a built-in backend (``env``, ``file``).
    """
    key = name.lower()
    if key in _BUILTIN_BACKENDS:
        raise ValueError(
            f"Cannot register secrets backend {name!r}: it is a built-in "
            f"({', '.join(sorted(_BUILTIN_BACKENDS))}) and cannot be shadowed."
        )
    _PROVIDER_FACTORIES[key] = factory
    logger.debug("Registered secrets provider backend %r", name)


def _discovered_entry_points() -> list[EntryPoint]:
    """Return every entry point in :data:`ENTRY_POINT_GROUP`.

    ``importlib.metadata`` is imported here, not at module scope: it is only
    needed when a non-built-in, unregistered backend is selected, and the
    default ``env`` path must not pay for scanning package metadata.
    """
    import importlib.metadata

    return list(importlib.metadata.entry_points(group=ENTRY_POINT_GROUP))


def _entry_point_origin(entry_point: EntryPoint) -> str:
    """Name the distribution that declared ``entry_point``, for error messages."""
    dist = getattr(entry_point, "dist", None)
    dist_name = getattr(dist, "name", None)
    return f"{dist_name} ({entry_point.value})" if dist_name else entry_point.value


def _ensure_provider(provider: object, source: str) -> SecretsProvider:
    """Return ``provider`` if it satisfies :class:`SecretsProvider`, else raise."""
    if not isinstance(provider, SecretsProvider):
        raise TypeError(
            f"Secrets backend {source} returned {type(provider).__name__}, "
            "which does not implement SecretsProvider (get_secret(name) -> "
            "SecretStr | None). Refusing to fall back to another backend."
        )
    return provider


def _provider_from_entry_point(
    backend: str, candidates: list[EntryPoint]
) -> SecretsProvider:
    """Load and build the single entry point named ``backend``; fail closed."""
    matches = [ep for ep in candidates if ep.name.lower() == backend]
    if len(matches) > 1:
        origins = ", ".join(sorted(_entry_point_origin(ep) for ep in matches))
        raise ValueError(
            f"Ambiguous secrets backend {backend!r}: {len(matches)} installed "
            f"packages declare it in the {ENTRY_POINT_GROUP!r} entry-point "
            f"group ({origins}). Uninstall all but one, or register the "
            "intended factory explicitly with register_secrets_provider()."
        )
    (entry_point,) = matches
    source = f"entry point {entry_point.name!r} from {_entry_point_origin(entry_point)}"
    try:
        factory = entry_point.load()
        provider = factory()
    except Exception as exc:
        # Only the exception type goes in the message: a provider's error text
        # may carry an address or credential. The cause stays chained.
        raise RuntimeError(
            f"Secrets backend {source} failed to load "
            f"({type(exc).__name__}). Refusing to fall back to another backend."
        ) from exc
    return _ensure_provider(provider, source)


def _build_provider(backend: str, secrets_dir: str | None) -> SecretsProvider:
    """Construct the provider for ``backend``.

    Precedence: built-ins, then explicit registrations, then installed entry
    points (looked up only when needed). Anything else raises.
    """
    backend = backend.lower()
    if backend == "env":
        return EnvSecretsProvider()
    if backend == "file":
        return FileSecretsProvider(secrets_dir)
    factory = _PROVIDER_FACTORIES.get(backend)
    if factory is not None:
        return _ensure_provider(factory(), f"registered backend {backend!r}")
    candidates = _discovered_entry_points()
    if any(ep.name.lower() == backend for ep in candidates):
        return _provider_from_entry_point(backend, candidates)
    discovered = sorted({ep.name.lower() for ep in candidates} - _BUILTIN_BACKENDS)
    raise ValueError(
        f"Unknown secrets backend {backend!r}. "
        f"Built-ins: {sorted(_BUILTIN_BACKENDS)}. "
        f"Registered: {sorted(_PROVIDER_FACTORIES)}. "
        f"Discovered via {ENTRY_POINT_GROUP!r} entry points: {discovered}. "
        "Install the package providing it or fix SECRETS_BACKEND."
    )


def get_secrets_provider() -> SecretsProvider:
    """Return the process-wide secrets provider, building it on first use.

    Backend selection reads ``SecurityConfig.secrets_backend``
    (``SECRETS_BACKEND``, default ``env``) and, for the file backend,
    ``SecurityConfig.secrets_dir`` (``SECRETS_DIR``). The result is cached;
    call :func:`reset_secrets_provider` in tests to force a rebuild (the
    security config is cached too, so a test changing the environment must
    reset both).
    """
    global _provider
    if _provider is None:
        # Imported here: core.config must stay importable without
        # core.security, and this runs only on the first secret read.
        from core.config.security import get_security_config

        config = get_security_config()
        _provider = _build_provider(config.secrets_backend, config.secrets_dir)
        # Log the resolved provider class, not the raw SECRETS_BACKEND value:
        # same information, no environment-derived string in the log line.
        logger.info("Initialized provider: %s", type(_provider).__name__)
    return _provider


def reset_secrets_provider() -> None:
    """Clear the cached provider. Intended for tests only."""
    global _provider
    _provider = None


#: Optional per-caller secret guard, installed by the plugin machinery.
#: ``core.security`` must not know what a plugin is, so the check arrives as a
#: callable that receives the secret's *name* — never its value — and raises to
#: refuse the read. Unset by default: exactly the behaviour this module had
#: before.
_SECRET_GUARD: Callable[[str], None] | None = None


def set_secret_guard(guard: Callable[[str], None] | None) -> None:
    """Install (or clear) the per-caller secret guard.

    Args:
        guard: Called with the secret name on every :func:`get_secret`; it
            raises to refuse the read. ``None`` removes the guard.
    """
    global _SECRET_GUARD
    _SECRET_GUARD = guard


def get_secret(name: str) -> SecretStr | None:
    """Convenience accessor delegating to the active provider.

    Raises:
        Exception: Whatever the installed guard raises when the caller is not
            permitted to read ``name``.
    """
    if _SECRET_GUARD is not None:
        _SECRET_GUARD(name)
    return get_secrets_provider().get_secret(name)


__all__ = [
    "EnvSecretsProvider",
    "FileSecretsProvider",
    "SecretsProvider",
    "get_secret",
    "get_secrets_provider",
    "register_secrets_provider",
    "reset_secrets_provider",
    "set_secret_guard",
]
