"""Which executable a stdio MCP server may be launched through.

An allowlist of *names* is only as strong as the lookup behind it. Resolving a
bare name through ``PATH`` hands the decision to whoever controls ``PATH`` (an
earlier entry, a relative ``.``), and comparing a fuzzy name (``python3.12``
against ``python``, any letter case) widens the list silently. This module
therefore resolves a command against a fixed list of trusted directories only,
matches names exactly, and refuses shells and exec-wrappers no matter what the
configuration says.
"""

from __future__ import annotations

import os
import stat
import sys

from baselith.core.config import get_mcp_config
from baselith.core.observability.logging import get_logger

logger = get_logger(__name__)

#: Shells, exec-wrappers, remote-login tools and script engines that turn "run
#: this program" into "run this string". Hard-coded on purpose: no setting can
#: lift it, because the setting is exactly what this list defends against.
DENIED_EXECUTABLES = frozenset(
    {
        # shells
        "sh", "bash", "zsh", "dash", "ash", "ksh", "mksh", "csh", "tcsh", "fish",
        "rbash", "busybox", "toybox", "cmd", "powershell", "pwsh", "wsl",
        "command", "exec", "eval", "builtin",
        # exec-wrappers: they run the *next* argument as a program
        "env", "xargs", "nohup", "nice", "ionice", "timeout", "time", "sudo",
        "su", "doas", "chroot", "setsid", "stdbuf", "taskset", "unshare",
        "nsenter", "flock", "watch", "script", "strace", "ltrace", "gdb",
        "parallel", "runuser", "setpriv", "chrt", "caffeinate", "arch", "open",
        # remote login / reverse-shell building blocks
        "ssh", "scp", "sftp", "rsh", "telnet", "nc", "ncat", "netcat", "socat",
        # engines whose whole purpose is evaluating a string or a script
        "perl", "ruby", "php", "lua", "tclsh", "awk", "gawk", "mawk", "find",
        "osascript", "wscript", "cscript", "mshta", "rundll32",
    }
)  # fmt: skip

#: Directories searched for a bare command name when the operator sets none.
_SYSTEM_BIN_DIRS = ("/usr/bin", "/bin", "/usr/local/bin", "/opt/homebrew/bin")

#: Prefixes a trusted system bin directory may legitimately symlink into
#: (Homebrew and distro packages keep the real file next to, not inside, bin/).
_SYSTEM_PREFIXES = ("/usr", "/usr/local", "/opt/homebrew")


def normalize_name(name: str) -> str:
    """The comparison form of an executable name: exact on POSIX, folded on Windows."""
    if os.name != "nt":
        return name
    lowered = name.lower()
    for suffix in (".exe", ".cmd", ".bat", ".com"):
        if lowered.endswith(suffix):
            return lowered[: -len(suffix)]
    return lowered


def is_denied(name: str) -> bool:
    """Whether *name* is a shell or exec-wrapper (case-insensitive on every OS).

    macOS and Windows file systems ignore case, so ``/bin/SH`` is a shell even
    though the POSIX allowlist comparison is case-sensitive.
    """
    base = os.path.basename(name).lower()
    for suffix in (".exe", ".cmd", ".bat", ".com"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
    return base in DENIED_EXECUTABLES


def split_list(raw: str) -> tuple[str, ...]:
    """Items of a comma- or ``os.pathsep``-separated setting, blanks dropped."""
    items = raw.replace(os.pathsep, ",").split(",") if raw else []
    return tuple(item.strip() for item in items if item.strip())


def allowed_names(allowed: frozenset[str] | None = None) -> frozenset[str]:
    """The exact-match name allowlist, minus entries that cannot be honoured.

    An entry carrying a path separator is not a name (it would let one string
    stand for a location), and a shell or wrapper stays out whatever the
    operator wrote.
    """
    names = (
        allowed if allowed is not None else get_mcp_config().allowed_command_basenames
    )
    kept: set[str] = set()
    for entry in names:
        if os.sep in entry or (os.altsep and os.altsep in entry) or "/" in entry:
            logger.warning(
                "mcp_stdio_allowlist_entry_ignored", entry=entry, reason="path"
            )
        elif is_denied(entry):
            logger.warning(
                "mcp_stdio_allowlist_entry_ignored", entry=entry, reason="denied"
            )
        else:
            kept.add(normalize_name(entry))
    return frozenset(kept)


def _real_dir(path: str) -> str | None:
    real = os.path.realpath(path)
    return real if os.path.isdir(real) else None


def trusted_bin_dirs() -> tuple[str, ...]:
    """Real paths of the directories a command may live in, in search order.

    ``MCP_STDIO_TRUSTED_BIN_DIRS`` replaces the built-in system list; the
    running interpreter's own directory is always trusted (this process already
    runs from it). Relative entries are ignored: their meaning is the cwd's.
    """
    raw = getattr(get_mcp_config(), "mcp_stdio_trusted_bin_dirs", "")
    configured = split_list(raw)
    if configured:
        candidates = list(configured)
    else:
        candidates = list(_SYSTEM_BIN_DIRS)
        candidates.append(os.path.join(sys.prefix, "bin"))
        conda = os.environ.get("CONDA_PREFIX")
        if conda:
            candidates.append(os.path.join(conda, "bin"))
    candidates.append(os.path.dirname(sys.executable))
    seen: list[str] = []
    for entry in candidates:
        if not os.path.isabs(entry):
            continue
        real = _real_dir(entry)
        if real is not None and real not in seen:
            seen.append(real)
    return tuple(seen)


def _trusted_roots(dirs: tuple[str, ...]) -> tuple[str, ...]:
    roots = list(dirs)
    roots.extend(
        os.path.realpath(p) for p in {sys.prefix, sys.base_prefix, sys.exec_prefix}
    )
    conda = os.environ.get("CONDA_PREFIX")
    if conda:
        roots.append(os.path.realpath(conda))
    if not getattr(get_mcp_config(), "mcp_stdio_trusted_bin_dirs", ""):
        roots.extend(os.path.realpath(p) for p in _SYSTEM_PREFIXES)
    return tuple(roots)


def is_inside(path: str, root: str) -> bool:
    """Whether *path* is *root* or below it (both already real paths)."""
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


def world_writable(path: str) -> bool:
    """Whether anyone may write *path* (POSIX mode check; never true on Windows)."""
    if os.name == "nt":
        return False
    return bool(os.stat(path).st_mode & stat.S_IWOTH)


def resolve_binary(executable: str) -> tuple[str, str]:
    """Locate *executable* in a trusted directory and vet the file behind it.

    A bare name is searched in :func:`trusted_bin_dirs` only - never ``PATH``.
    An absolute path is accepted only when its directory is one of them. The
    file behind it must be executable, must not be world-writable (nor sit in a
    world-writable directory), must not be a shell or wrapper once symlinks are
    followed, and must not be a symlink leaving the trusted prefixes.

    Returns:
        ``(path, real_path)``. *path* is what gets exec'd (the trusted
        location, not the symlink target - a venv interpreter finds its
        ``pyvenv.cfg`` from the path it was started by).

    Raises:
        ValueError: With a message naming the refused property.
    """
    if "\x00" in executable:
        raise ValueError("MCP command must not contain NUL bytes")
    dirs = trusted_bin_dirs()
    if os.path.isabs(executable):
        parent = os.path.realpath(os.path.dirname(executable))
        if parent not in dirs:
            raise ValueError(
                f"MCP command '{executable}' is not in a trusted bin directory "
                f"({list(dirs)}); set MCP_STDIO_TRUSTED_BIN_DIRS to extend the list"
            )
        candidate = os.path.join(parent, os.path.basename(executable))
    elif os.sep in executable or (os.altsep and os.altsep in executable):
        raise ValueError(
            f"MCP command '{executable}' must be an absolute path or a bare "
            "executable name found in a trusted bin directory"
        )
    else:
        candidate = next(
            (
                path
                for path in (os.path.join(d, executable) for d in dirs)
                if os.path.isfile(path) and os.access(path, os.X_OK)
            ),
            "",
        )
        if not candidate:
            raise ValueError(
                f"MCP command '{executable}' was not found in any trusted bin "
                f"directory ({list(dirs)}); PATH is never searched. Set "
                "MCP_STDIO_TRUSTED_BIN_DIRS if it lives elsewhere."
            )
    real = os.path.realpath(candidate)
    if not (os.path.isfile(real) and os.access(real, os.X_OK)):
        raise ValueError(f"MCP command '{executable}' is not an executable file")
    if is_denied(real):
        raise ValueError(
            f"MCP command '{executable}' resolves to '{os.path.basename(real)}', "
            "a shell or exec-wrapper that is never launchable"
        )
    if not any(is_inside(real, root) for root in _trusted_roots(dirs)):
        raise ValueError(
            f"MCP command '{executable}' resolves to '{real}', outside the "
            "trusted bin directories"
        )
    if world_writable(real) or world_writable(os.path.dirname(real)):
        raise ValueError(
            f"MCP command '{executable}' ('{real}') is world-writable or sits "
            "in a world-writable directory"
        )
    return candidate, real


__all__ = [
    "DENIED_EXECUTABLES",
    "allowed_names",
    "is_denied",
    "is_inside",
    "normalize_name",
    "resolve_binary",
    "split_list",
    "trusted_bin_dirs",
    "world_writable",
]
