"""Run a pytest file against a staged repository workspace inside the sandbox.

The files are written to a private host directory, mounted read-only at ``/work`` and copied to
a fresh directory under ``/tmp`` before anything runs, so the code under test never writes back
to the host. A mount, not the script, carries them: Linux caps one argument at 128 KiB, and a
repository file alone can come close. The reporter and the keyed report line are the ones of
:mod:`baselith.plugins.coding_agent.runner.harness`
(:func:`~baselith.plugins.coding_agent.runner.harness.runner_source`), and so is the assertion count:
pytest's assertion-pass hook is enabled on the command line (``-c`` points pytest at an empty
config), and the test file is an initial path, so pytest rewrites its asserts whatever its name.
"""

from __future__ import annotations

import os
import stat
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath

from baselith.plugins.coding_agent.runner.harness import (
    ASSERTION_HOOK_OPTION,
    runner_source,
)

#: Where the staged workspace is mounted inside the sandbox.
WORK_DIR = "/work"
#: The most a workspace may hold, in UTF-8 bytes (the sandbox's ``/tmp`` is a 64 MiB tmpfs).
WORKSPACE_MAX_BYTES = 1_000_000
#: Files that configure pytest or plug into it; a workspace never carries them.
PYTEST_CONFIG_NAMES = frozenset(
    {"conftest.py", "pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml"}
)

#: The setup before the shared runner. pytest is imported before the workspace is on
#: ``sys.path`` (or is the working directory): a workspace file named ``pytest.py`` or
#: ``_pytest/`` would otherwise be the pytest that runs the tests and fills in the report.
_TEMPLATE = """\
import json, os, shutil, sys, tempfile
sys.dont_write_bytecode = True
os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
os.environ.pop("PYTEST_ADDOPTS", None)
import pytest
_root = tempfile.mkdtemp(prefix="coding-agent-", dir="/tmp")
shutil.copytree({work!r}, _root, dirs_exist_ok=True)
os.chdir(_root)
for _r in reversed({roots!r}):
    sys.path.insert(0, os.path.join(_root, _r) if _r else _root)
"""


#: The "other" bits below are the point, not an oversight: the sandbox runs as another
#: user, so it reads a workspace through them. Scanners flag every chmod that sets them.
#: Owner rwx, others r-x: the sandbox user lists and reads a workspace, never writes it.
_DIR_MODE = 0o705
#: Owner rw, others r.
_FILE_MODE = 0o604
#: Owner rwx, others x only: workspaces below it can be entered, not listed.
_PARENT_MODE = 0o701


def _parent_dir() -> str:
    return os.path.join(tempfile.gettempdir(), f"coding-agent-{os.getuid()}")


def _private_parent() -> str:
    """This user's own directory for workspaces, made and checked before every use.

    Raises:
        ValueError: The path is a symlink, or a directory someone else owns.
    """
    parent = _parent_dir()
    try:
        os.mkdir(parent, _PARENT_MODE)
    except FileExistsError:
        pass
    found = os.lstat(parent)
    if (
        stat.S_ISLNK(found.st_mode)
        or not stat.S_ISDIR(found.st_mode)
        or found.st_uid != os.getuid()
    ):
        raise ValueError(f"unsafe staging directory {parent!r}")
    # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions
    os.chmod(parent, _PARENT_MODE)
    return parent


def _safe(rel: str) -> PurePosixPath:
    path = PurePosixPath(rel)
    if not rel or path.is_absolute() or ".." in path.parts or "\\" in rel:
        raise ValueError(f"not a repository-relative path: {rel!r}")
    return path


def stage(files: Mapping[str, str]) -> str:
    """Write ``files`` into a fresh private host directory and return its path.

    The caller removes the directory after the run.

    Raises:
        ValueError: A path escapes the directory, a file would configure pytest (a conftest or a
            config file could load a plugin that forges the report), or the files exceed
            ``WORKSPACE_MAX_BYTES``.
    """
    for name in files:
        if PurePosixPath(name).name in PYTEST_CONFIG_NAMES:
            raise ValueError(
                f"{name!r} would configure pytest; a workspace never carries it"
            )
    total = sum(len(text.encode("utf-8")) for text in files.values())
    if total > WORKSPACE_MAX_BYTES:
        raise ValueError(f"the workspace is too large ({total} bytes)")
    checked = {_safe(rel): text for rel, text in files.items()}
    root = tempfile.mkdtemp(prefix="coding-ws-", dir=_private_parent())
    # The sandbox runs as another user (uid 65534) and a Linux bind mount keeps host modes:
    # it may enter, list and read the workspace (others r-x / r--), never write it, and the
    # parent above it cannot be listed, so nobody else finds the random name.
    # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions
    os.chmod(root, _DIR_MODE)
    for path, text in checked.items():
        target = os.path.join(root, *path.parts)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        for parent in path.parents:
            if str(parent) != ".":
                # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions
                os.chmod(os.path.join(root, *parent.parts), _DIR_MODE)
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(target, _FILE_MODE)
    return root


def build_workspace_script(test_path: str, roots: Sequence[str], *, key: str) -> str:
    """The ``python -c`` script that copies ``/work`` to ``/tmp`` and runs ``test_path``.

    Raises:
        ValueError: ``test_path`` is not repository-relative.
    """
    # Python source: ``os.devnull`` and ``_root`` are the script's own names.
    pytest_args = (
        '["-q", "-p", "no:cacheprovider", "--noconftest", "-c", os.devnull, "--no-header", '
        '"-o", "console_output_style=classic", "--rootdir", _root, '
        f'"-o", {ASSERTION_HOOK_OPTION!r}, {str(_safe(test_path))!r}]'
    )
    return _TEMPLATE.format(work=WORK_DIR, roots=list(roots)) + runner_source(
        pytest_args, key=key
    )


__all__ = [
    "PYTEST_CONFIG_NAMES",
    "WORKSPACE_MAX_BYTES",
    "WORK_DIR",
    "build_workspace_script",
    "stage",
]
