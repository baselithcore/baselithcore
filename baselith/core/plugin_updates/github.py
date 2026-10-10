"""GitHub repository helpers shared by the release tooling and the update checker."""

from __future__ import annotations

import re

_GITHUB_REPO = re.compile(
    r"^(?:git@github\.com:|https://github\.com/|ssh://git@github\.com/)"
    r"(?P<owner>[A-Za-z0-9_.-]+)/(?P<name>[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)


def repo_slug(repo: str) -> str:
    """Return the ``owner/name`` slug of a GitHub repository URL.

    Args:
        repo: An SSH (``git@github.com:o/r.git``) or HTTPS
            (``https://github.com/o/r[.git]``) GitHub URL.

    Returns:
        The ``owner/name`` slug.

    Raises:
        ValueError: If ``repo`` is not a parseable GitHub repository URL.
    """
    match = _GITHUB_REPO.match(repo.strip())
    if match is None:
        raise ValueError(f"not a GitHub repository URL: {repo!r}")
    return f"{match['owner']}/{match['name']}"


__all__ = ["repo_slug"]
