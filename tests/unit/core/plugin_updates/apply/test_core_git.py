"""The core driver's git seam, against a real throwaway repository."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any

import pytest

from baselith.core.plugin_updates.apply.drivers import _git
from baselith.core.plugin_updates.apply.drivers._git import (
    STDOUT_CAP,
    GitError,
    checkout_detached,
    fetch_commit,
    head_sha,
    lock_sha256,
    porcelain,
    subprocess_git,
)

_ENV = {
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@e",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@e",
    "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
}


def _sh(repo: Path, *argv: str) -> str:
    done = subprocess.run(
        ["git", *argv],  # noqa: S607
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env=_ENV,
    )
    return done.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str, str]:
    for key, value in _ENV.items():
        monkeypatch.setenv(key, value)
    root = tmp_path / "checkout"
    root.mkdir()
    _sh(root, "init", "-q", "-b", "main")
    (root / "uv.lock").write_text("version = 1\n")
    (root / "app.py").write_text("V = 1\n")
    _sh(root, "add", ".")
    _sh(root, "commit", "-qm", "one")
    first = _sh(root, "rev-parse", "HEAD")
    (root / "app.py").write_text("V = 2\n")
    (root / "uv.lock").write_text("version = 2\n")
    _sh(root, "commit", "-qam", "two")
    second = _sh(root, "rev-parse", "HEAD")
    return root, first, second


def test_head_and_porcelain(repo: tuple[Path, str, str]) -> None:
    root, _, second = repo
    assert head_sha(subprocess_git, root) == second
    assert porcelain(subprocess_git, root) == ""
    (root / "app.py").write_text("V = 3\n")
    (root / "new.txt").write_text("untracked\n")
    assert porcelain(subprocess_git, root) == " M app.py\n"


def test_head_outside_a_checkout_raises(tmp_path: Path) -> None:
    with pytest.raises(GitError):
        head_sha(subprocess_git, tmp_path)


def test_checkout_detached_moves_head_and_back(repo: tuple[Path, str, str]) -> None:
    root, first, second = repo
    assert checkout_detached(subprocess_git, root, first)
    assert head_sha(subprocess_git, root) == first
    assert (root / "app.py").read_text() == "V = 1\n"
    assert checkout_detached(subprocess_git, root, second)
    assert head_sha(subprocess_git, root) == second


def test_checkout_never_overwrites_local_changes(repo: tuple[Path, str, str]) -> None:
    root, first, second = repo
    (root / "app.py").write_text("local edit\n")
    assert not checkout_detached(subprocess_git, root, first)
    assert head_sha(subprocess_git, root) == second
    assert (root / "app.py").read_text() == "local edit\n"


def test_fetch_refuses_when_sha_not_reachable(
    repo: tuple[Path, str, str], tmp_path: Path
) -> None:
    root, first, _ = repo
    assert fetch_commit(subprocess_git, root, None, first)  # already present
    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", "-q", "--depth", "1", f"file://{root}", str(clone)],  # noqa: S607
        check=True,
        env=_ENV,
    )
    assert fetch_commit(subprocess_git, clone, f"file://{root}", first)
    assert not fetch_commit(subprocess_git, clone, f"file://{root}", "e" * 40)
    assert not fetch_commit(subprocess_git, clone, None, "not-a-sha")


def test_lock_sha256_matches_hashlib(repo: tuple[Path, str, str]) -> None:
    root, _, _ = repo
    expected = hashlib.sha256((root / "uv.lock").read_bytes()).hexdigest()
    assert lock_sha256(root) == expected
    (root / "uv.lock").unlink()
    with pytest.raises(OSError):
        lock_sha256(root)


def test_git_env_is_scrubbed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BASELITH_PLUGIN_SIGNING_KEY", "k")
    monkeypatch.setenv("PLUGIN_UPDATE_GITHUB_TOKEN", "t")
    monkeypatch.setenv("UPDATE_APPLY_BACKUP_COMMAND", "b")
    monkeypatch.setenv("ADMIN_PASS", "p")
    monkeypatch.setenv("KEEP_ME", "1")
    seen: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        seen.update(kwargs, argv=argv)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(_git.subprocess, "run", fake_run)
    subprocess_git(["status"], cwd=tmp_path, timeout=5)
    env = seen["env"]
    assert seen["argv"] == ["git", "status"] and seen["shell"] is False
    assert env["GIT_TERMINAL_PROMPT"] == "0" and env["KEEP_ME"] == "1"
    for secret in (
        "BASELITH_PLUGIN_SIGNING_KEY",
        "PLUGIN_UPDATE_GITHUB_TOKEN",
        "UPDATE_APPLY_BACKUP_COMMAND",
        "ADMIN_PASS",
    ):
        assert secret not in env


def test_stdout_is_capped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        kwargs["stdout"].write(b"x" * (STDOUT_CAP + 4096))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(_git.subprocess, "run", fake_run)
    result = subprocess_git(["log"], cwd=tmp_path, timeout=5)
    assert result.returncode == 0 and len(result.stdout) == STDOUT_CAP


def test_a_timeout_is_minus_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_run(argv: list[str], **kwargs: Any) -> None:
        raise subprocess.TimeoutExpired(argv, 1)

    monkeypatch.setattr(_git.subprocess, "run", fake_run)
    assert subprocess_git(["fetch"], cwd=tmp_path, timeout=1) == (-1, "")


def test_fetch_judges_the_commit_even_when_the_tag_fetch_fails(tmp_path: Path) -> None:
    """``fetch --tags`` exits non-zero when any upstream tag moved; the SHA decides."""
    from baselith.core.plugin_updates.apply.drivers._git import GitResult

    sha = "a" * 40
    state = {"present": False}
    calls: list[list[str]] = []

    def git(args: Any, *, cwd: Path, timeout: float) -> GitResult:
        argv = list(args)
        calls.append(argv)
        if argv[0] == "rev-parse":
            return GitResult(0, f"{sha}\n") if state["present"] else GitResult(1, "")
        if "--no-tags" in argv:
            return GitResult(128, "")  # this server refuses a fetch by SHA
        state["present"] = True  # the commit arrived with the tags...
        return GitResult(1, "")  # ...but a moved tag made fetch exit 1

    assert fetch_commit(git, tmp_path, None, sha)
    assert any("--tags" in c for c in calls)
