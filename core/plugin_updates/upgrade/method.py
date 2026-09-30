"""Which installation method this deployment uses."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from core.config.plugin_updates import PluginUpdateConfig

from ..upgrade_models import INSTALL_METHODS, InstallMethod

#: Substrings of ``/proc/1/cgroup`` that mean PID 1 runs in a container.
_CONTAINER_HINTS = ("docker", "containerd", "kubepods", "libpod", "podman")
#: Files a container runtime creates in the container (Docker, Podman).
_CONTAINER_MARKERS = (".dockerenv", "run/.containerenv")
#: The directory of the running ``core`` package.
CORE_PACKAGE_DIR = Path(__file__).resolve().parents[2]
#: Directories an installed (non-editable) distribution lives in.
_INSTALLED_DIRS = frozenset({"site-packages", "dist-packages"})


def _in_container(root: Path) -> bool:
    if any((root / marker).exists() for marker in _CONTAINER_MARKERS):
        return True
    try:
        cgroup = (root / "proc" / "1" / "cgroup").read_text(
            encoding="utf-8", errors="replace"
        )
    except OSError:
        return False
    return any(hint in cgroup for hint in _CONTAINER_HINTS)


def is_source_checkout(package_dir: Path) -> bool:
    """True when ``core`` runs from a source checkout, not an installed wheel.

    A checkout (editable install or ``PYTHONPATH``) keeps the package outside
    ``site-packages``/``dist-packages``, or has a ``.git`` beside it.

    Args:
        package_dir: The directory of the ``core`` package.
    """
    if (package_dir.parent / ".git").exists():
        return True
    return not _INSTALLED_DIRS.intersection(package_dir.parts)


def detect_install_method(
    config: PluginUpdateConfig,
    *,
    environ: Mapping[str, str] | None = None,
    root: Path = Path("/"),
    package_dir: Path = CORE_PACKAGE_DIR,
) -> tuple[InstallMethod, bool]:
    """The installation method, and whether it was detected rather than set.

    ``SYSTEM_INSTALL_METHOD`` wins. Without it, an instructions file means the
    operator documents the procedure (``custom``); otherwise Kubernetes
    (``KUBERNETES_SERVICE_HOST``) means the Helm chart, a container
    (``/.dockerenv``, Podman's ``/run/.containerenv`` or a container cgroup)
    means Docker Compose, a source checkout on a host means ``source``, and
    anything else a pip installation on a host.

    Args:
        config: The update settings.
        environ: The environment to inspect (``os.environ`` when omitted).
        root: Filesystem root for the container probes (tests pass a tmp dir).
        package_dir: The ``core`` package directory (tests pass a tmp dir).
    """
    for method in INSTALL_METHODS:
        if config.install_method == method:
            return method, False
    if config.upgrade_instructions_file is not None:
        return "custom", False
    env = os.environ if environ is None else environ
    if env.get("KUBERNETES_SERVICE_HOST", "").strip():
        return "helm", True
    if _in_container(root):
        return "docker", True
    if is_source_checkout(package_dir):
        return "source", True
    return "pip", True


__all__ = ["CORE_PACKAGE_DIR", "detect_install_method", "is_source_checkout"]
