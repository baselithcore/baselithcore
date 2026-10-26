"""
Shared Docker runtime policy for sandboxed code execution.

One place decides what an untrusted-code container may do, so the three call
sites that start one (:mod:`baselith.core.services.sandbox.service`,
:mod:`~baselith.core.services.sandbox.pool` and :mod:`~baselith.core.services.sandbox.streaming`)
cannot drift apart on hardening.
"""

from __future__ import annotations

from typing import Any

from .resources import Resources, clamp

#: Largest single file a sandboxed process may write (the ``fsize`` rlimit).
FSIZE_LIMIT_BYTES = 64 * 1024 * 1024

#: Resource ceilings applied as kernel ``rlimit``s, as ``(name, value)``.
#: These bound what dropped capabilities and ``pids_limit`` do not:
#:
#: * ``nofile`` — open file descriptors. Without it a single process can
#:   exhaust the host's descriptor table even with no network and no writable
#:   filesystem (``/tmp`` alone is enough).
#: * ``nproc`` — processes/threads for the container's uid. ``pids_limit`` is
#:   the cgroup-level cap; ``nproc`` also stops a thread bomb from being
#:   *attempted* thousands of times per second.
#: * ``fsize`` — maximum size of any single file. The root filesystem is
#:   read-only and ``/tmp`` is a 64 MiB tmpfs, so this mainly turns a runaway
#:   write into an immediate ``EFBIG`` instead of filling that tmpfs.
_ULIMITS: tuple[tuple[str, int], ...] = (
    ("nofile", 1024),
    ("nproc", 256),
    ("fsize", FSIZE_LIMIT_BYTES),
)


def _build_ulimits() -> list[Any]:
    """Return the rlimit set as docker ``Ulimit`` objects, or plain dicts.

    ``docker.types.Ulimit`` is the documented shape, but the ``docker`` SDK is
    an optional dependency here (the ``sbx`` provider needs none of it). The
    API's ``create_host_config`` coerces ``{"Name", "Soft", "Hard"}`` mappings
    through ``Ulimit(**entry)`` anyway, so the fallback is wire-identical and
    this module stays importable — and testable — without the SDK installed.
    """
    try:
        from docker.types import Ulimit
    except ImportError:
        return [
            {"Name": name, "Soft": value, "Hard": value} for name, value in _ULIMITS
        ]
    return [Ulimit(name=name, soft=value, hard=value) for name, value in _ULIMITS]


def build_sandbox_runtime_kwargs(
    enable_network: bool | None = None, resources: Resources | None = None
) -> dict[str, Any]:
    """Return conservative Docker runtime options for untrusted code.

    The container gets no network (unless ``SANDBOX_ENABLE_NETWORK`` opts in),
    no capabilities, no privilege escalation, a
    **read-only root filesystem**, a non-root uid and hard resource ceilings.
    ``/tmp`` is the single writable path — a ``noexec,nosuid`` tmpfs, so code
    can stage scratch files there but cannot write a binary and run it.

    Memory (128 MiB) and the ``/tmp`` size (64 MiB) are the defaults; a caller
    may pass ``resources`` to ask for more, bounded by the operator's
    ``SANDBOX_MAX_MEMORY_MB`` / ``SANDBOX_MAX_TMPFS_MB`` ceilings (see
    :func:`~baselith.core.services.sandbox.resources.clamp`). Nothing else is
    adjustable per call.

    ``user`` is ``65534:65534`` (``nobody:nogroup`` on Debian-derived images,
    which the bundled ``Dockerfile.sandbox`` is): a uid that owns nothing, so
    even a filesystem left writable by mistake grants nothing to write to.

    ``working_dir`` must therefore be ``/tmp`` and not the image's ``WORKDIR``.
    ``useradd -m`` on Debian bookworm creates ``/home/sandbox`` **mode 0700**
    owned by uid 1000, so uid 65534 can neither read nor enter it: leaving the
    cwd there makes every relative-path write — and some tooling's cwd probe —
    fail with ``PermissionError``, for no isolation benefit that ``read_only``
    does not already provide.

    Args:
        enable_network: Give the container the default bridge network.
            ``None`` (the default) reads ``SANDBOX_ENABLE_NETWORK``, which is
            off: sandboxed code gets egress only when an operator opts in.
        resources: Optional per-call memory / tmpfs request, clamped to the
            configured ceilings. ``None`` keeps the defaults.

    Returns:
        Keyword arguments to splat into ``containers.run`` / ``containers.create``.
    """
    from baselith.core.config.sandbox import get_sandbox_config

    config = get_sandbox_config()
    if enable_network is None:
        enable_network = config.enable_network
    memory_mb, tmpfs_mb, _ = clamp(resources, config)
    tmpfs_options = f"rw,noexec,nosuid,size={tmpfs_mb}m"

    return {
        "network_mode": "bridge" if enable_network else "none",
        "mem_limit": f"{memory_mb}m",
        "cpu_period": 100000,
        "cpu_quota": 50000,
        "security_opt": ["no-new-privileges:true"],
        "cap_drop": ["ALL"],
        "pids_limit": 64,
        "read_only": True,
        "user": "65534:65534",
        "ulimits": _build_ulimits(),
        # cwd on the writable tmpfs — the image's WORKDIR is 0700 uid 1000.
        "working_dir": "/tmp",  # nosec B108  # noqa: S108
        "tmpfs": {"/tmp": tmpfs_options},  # nosec B108  # noqa: S108
    }
