"""The core driver's git seam: a capturing command runner and the few git steps it needs.

:class:`~.._io.CommandRunner` returns only an exit code, so git state (the
HEAD commit, the tracked-file status) needs a second seam that captures
stdout. :func:`subprocess_git` runs ``git <argv>`` without a shell, with the
environment scrubbed of every credential the updater holds (the signing key,
the GitHub token, the operator passwords, every ``UPDATE_APPLY_*`` setting),
``GIT_TERMINAL_PROMPT=0`` so a fetch never waits for a password, and stdout
capped at 1 MiB (spooled to a temporary file, never held whole in memory).
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import subprocess  # nosec B404 - argv lists only, never a shell
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import NamedTuple, Protocol

from .._io import _WITHHELD_FROM_SCHEMA_INIT, _WITHHELD_PREFIXES

logger = logging.getLogger(__name__)

#: Largest stdout kept from one git command.
STDOUT_CAP = 1 << 20
#: Never handed to git: hooks and credential helpers run with its environment.
SCRUBBED_ENV = frozenset({"BASELITH_PLUGIN_SIGNING_KEY", *_WITHHELD_FROM_SCHEMA_INIT})
_SHA = re.compile(r"^[0-9a-f]{40}$")
_GIT_TIMEOUT = 120.0
_FETCH_TIMEOUT = 600.0
LOCK_FILE = "uv.lock"


class GitResult(NamedTuple):
    """Exit code (``-1``: could not run or timed out) and captured stdout."""

    returncode: int
    stdout: str


class GitRunner(Protocol):
    """Runs ``git <argv>`` in ``cwd``."""

    def __call__(
        self, argv: Sequence[str], *, cwd: Path, timeout: float
    ) -> GitResult: ...


class GitError(RuntimeError):
    """A git step the driver relies on failed (the message holds no path)."""


def git_env(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """``environ`` (default: this process's) minus every secret, never prompting."""
    source = os.environ if environ is None else environ
    env = {
        k: v
        for k, v in source.items()
        if k not in SCRUBBED_ENV and not k.startswith(_WITHHELD_PREFIXES)
    }
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def subprocess_git(argv: Sequence[str], *, cwd: Path, timeout: float) -> GitResult:
    """Run ``git <argv>`` without a shell; stdout capped at :data:`STDOUT_CAP`.

    Only ``git`` and its subcommand are logged on failure.
    """
    with tempfile.TemporaryFile() as out:
        try:
            done = subprocess.run(  # nosec B603 B607 - argv list, shell=False
                ["git", *argv],  # noqa: S607 - the host's git, from PATH
                cwd=cwd,
                env=git_env(),
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
                check=False,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.error(
                "core_update_git_failed command=%s error=%s",
                argv[0] if argv else "",
                type(exc).__name__,
            )
            return GitResult(-1, "")
        out.seek(0)
        text = out.read(STDOUT_CAP).decode("utf-8", "replace")
    return GitResult(done.returncode, text)


def head_sha(git: GitRunner, cwd: Path) -> str:
    """The checkout's HEAD commit.

    Raises:
        GitError: Not a git checkout, or HEAD does not resolve.
    """
    result = git(
        ["rev-parse", "--verify", "HEAD^{commit}"], cwd=cwd, timeout=_GIT_TIMEOUT
    )
    sha = result.stdout.strip()
    if result.returncode != 0 or not _SHA.fullmatch(sha):
        raise GitError("the checkout's HEAD cannot be read")
    return sha


def porcelain(git: GitRunner, cwd: Path) -> str:
    """``git status --porcelain --untracked-files=no`` of the checkout.

    Raises:
        GitError: The status cannot be read.
    """
    result = git(
        ["status", "--porcelain", "--untracked-files=no"], cwd=cwd, timeout=_GIT_TIMEOUT
    )
    if result.returncode != 0:
        raise GitError("the checkout's status cannot be read")
    return result.stdout


def _resolves_to(git: GitRunner, cwd: Path, sha: str) -> bool:
    result = git(
        ["rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}"],
        cwd=cwd,
        timeout=_GIT_TIMEOUT,
    )
    return result.returncode == 0 and result.stdout.strip() == sha


def fetch_commit(git: GitRunner, cwd: Path, remote_url: str | None, sha: str) -> bool:
    """Make commit ``sha`` present locally; True only when exactly it is.

    A commit already present is not fetched again. Otherwise the commit is
    fetched by SHA (no tags, no ref update) from ``remote_url`` or ``origin``,
    then resolved again: whatever arrived, only ``sha`` itself counts.
    """
    if not _SHA.fullmatch(sha):
        return False
    if _resolves_to(git, cwd, sha):
        return True
    remote = remote_url or "origin"
    fetched = git(
        ["fetch", "--quiet", "--no-tags", remote, sha], cwd=cwd, timeout=_FETCH_TIMEOUT
    )
    if fetched.returncode == 0 and _resolves_to(git, cwd, sha):
        return True
    # A server that refuses a fetch by SHA still serves its tags: the commit
    # is then judged by its SHA alone, so a moved tag cannot redirect it. The
    # exit code is not: `fetch --tags` fails ("would clobber existing tag")
    # whenever any upstream tag moved, even though the commit arrived.
    git(["fetch", "--quiet", "--tags", remote], cwd=cwd, timeout=_FETCH_TIMEOUT)
    return _resolves_to(git, cwd, sha)


def checkout_detached(git: GitRunner, cwd: Path, sha: str) -> bool:
    """Check out ``sha`` detached; True only when HEAD is then exactly ``sha``.

    Never forced: git refuses a checkout that would overwrite local changes,
    and HEAD then stays where it was.
    """
    if not _SHA.fullmatch(sha):
        return False
    result = git(
        ["checkout", "--quiet", "--detach", sha], cwd=cwd, timeout=_GIT_TIMEOUT
    )
    if result.returncode != 0:
        return False
    try:
        return head_sha(git, cwd) == sha
    except GitError:
        return False


def checkout_root() -> Path:
    """The directory holding the ``baselith`` package (the checkout on a source host)."""
    import baselith.core

    return Path(baselith.core.__file__).resolve().parents[2]


def lock_sha256(cwd: Path) -> str:
    """SHA-256 of the checkout's ``uv.lock``.

    Raises:
        OSError: The file is missing or unreadable.
    """
    digest = hashlib.sha256()
    with (cwd / LOCK_FILE).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "LOCK_FILE",
    "SCRUBBED_ENV",
    "STDOUT_CAP",
    "GitError",
    "GitResult",
    "GitRunner",
    "checkout_detached",
    "checkout_root",
    "fetch_commit",
    "git_env",
    "head_sha",
    "lock_sha256",
    "porcelain",
    "subprocess_git",
]
