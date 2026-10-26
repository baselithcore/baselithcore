"""Bounded per-call resource requests for sandboxed code.

A caller may ask for more memory, scratch space or time than the defaults
(for example to process an uploaded document), but never more than the
operator-set ceilings ``SANDBOX_MAX_MEMORY_MB``, ``SANDBOX_MAX_TMPFS_MB`` and
``SANDBOX_MAX_TIMEOUT``. The ceilings default to the historical hard-coded
values, so nothing changes until an operator raises them.

Everything else the container policy enforces (no capabilities, read-only
root, ``noexec`` tmpfs, ``pids_limit``, ulimits) is deliberately *not*
adjustable per call.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from baselith.core.config.sandbox import SandboxConfig

#: Memory, in MiB, a run gets when the caller does not ask for more.
DEFAULT_MEMORY_MB = 128
#: Writable ``/tmp`` size, in MiB, a run gets when the caller does not ask.
DEFAULT_TMPFS_MB = 64


@dataclass(frozen=True)
class Resources:
    """A caller's request for sandbox resources; ``None`` means "the default".

    Attributes:
        memory_mb: Container memory limit in MiB.
        tmpfs_mb: Size of the writable ``/tmp`` tmpfs in MiB.
        timeout_s: Wall-clock execution timeout in seconds.
    """

    memory_mb: int | None = None
    tmpfs_mb: int | None = None
    timeout_s: int | None = None


def clamp(resources: Resources | None, config: SandboxConfig) -> tuple[int, int, int]:
    """Resolve a request against the defaults and the operator's ceilings.

    Each value is ``min(requested or default, ceiling)``. The defaults are
    128 MiB of memory, 64 MiB of tmpfs and ``config.timeout`` seconds; the
    ceilings are ``config.max_memory_mb``, ``config.max_tmpfs_mb`` and
    ``config.max_timeout``. A ceiling below a default lowers that default too.

    Args:
        resources: The caller's request, or ``None`` for no request.
        config: The sandbox configuration that carries the ceilings.

    Returns:
        ``(memory_mb, tmpfs_mb, timeout_s)``.
    """
    request = resources or Resources()
    return (
        min(request.memory_mb or DEFAULT_MEMORY_MB, config.max_memory_mb),
        min(request.tmpfs_mb or DEFAULT_TMPFS_MB, config.max_tmpfs_mb),
        min(request.timeout_s or config.timeout, config.max_timeout),
    )


def effective_timeout(
    timeout: int | None, resources: Resources | None, config: SandboxConfig
) -> int:
    """Return the timeout a run is held to.

    Without ``resources`` this is the historical rule — the explicit
    ``timeout`` else ``config.timeout`` — and is not clamped. Once the caller
    passes ``resources`` the result is bounded by ``config.max_timeout``: the
    request's own ``timeout_s`` wins, then the explicit ``timeout``, then
    ``config.timeout``.

    Args:
        timeout: The explicit ``timeout`` argument of the execute call.
        resources: The caller's per-call resource request, if any.
        config: The sandbox configuration.

    Returns:
        The timeout in seconds.
    """
    if resources is None:
        return timeout or config.timeout
    if resources.timeout_s is None and timeout:
        resources = replace(resources, timeout_s=timeout)
    return clamp(resources, config)[2]
