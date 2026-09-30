from __future__ import annotations

import pytest

from core.plugin_updates.github import repo_slug


@pytest.mark.parametrize(
    "url",
    [
        "git@github.com:baselithcore/plugin-example.git",
        "https://github.com/baselithcore/plugin-example.git",
        "https://github.com/baselithcore/plugin-example",
    ],
)
def test_repo_slug(url: str) -> None:
    assert repo_slug(url) == "baselithcore/plugin-example"


@pytest.mark.parametrize(
    "url", ["https://gitlab.com/o/r.git", "not a url", "https://github.com/o", ""]
)
def test_repo_slug_rejects_non_github(url: str) -> None:
    with pytest.raises(ValueError):
        repo_slug(url)
