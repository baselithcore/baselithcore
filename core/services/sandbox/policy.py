"""
Shared Docker runtime policy for sandboxed code execution.

One place decides what an untrusted-code container may do, so the three call
sites that start one (:mod:`core.services.sandbox.service`,
:mod:`~core.services.sandbox.pool` and :mod:`~core.services.sandbox.streaming`)
cannot drift apart on hardening.
"""

from __future__ import annotations

from typing import Any

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
    ("fsize", 64 * 1024 * 1024),
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


def build_sandbox_runtime_kwargs(enable_network: bool | None = None) -> dict[str, Any]:
    """Return conservative Docker runtime options for untrusted code.

    The container gets no network (unless ``SANDBOX_ENABLE_NETWORK`` opts in),
    no capabilities, no privilege escalation, a
    **read-only root filesystem**, a non-root uid and hard resource ceilings.
    ``/tmp`` is the single writable path — a ``noexec,nosuid`` tmpfs, so code
    can stage scratch files there but cannot write a binary and run it.

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

    Returns:
        Keyword arguments to splat into ``containers.run`` / ``containers.create``.
    """
    if enable_network is None:
        from core.config.sandbox import get_sandbox_config

        enable_network = get_sandbox_config().enable_network

    return {
        "network_mode": "bridge" if enable_network else "none",
        "mem_limit": "128m",
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
        "tmpfs": {"/tmp": "rw,noexec,nosuid,size=64m"},  # nosec B108  # noqa: S108
    }
