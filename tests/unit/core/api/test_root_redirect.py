"""Tests for the opt-in landing redirect on ``/``.

Two halves, matching where the behaviour lives: the configuration layer decides
*whether* a target is safe to serve (an unvalidated one would make the root an
open redirect), and the router decides *how* it is served.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from core.api.root_redirect import create_root_redirect_router
from core.config.app import AppConfig


def _client(target: str) -> TestClient:
    app = FastAPI()
    app.include_router(create_root_redirect_router(target))
    return TestClient(app)


def test_root_redirects_to_target_without_auth() -> None:
    """The bare hostname sends an anonymous visitor to the landing."""
    response = _client("/console/").get("/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/console/"


def test_redirect_is_temporary_not_permanent() -> None:
    """307, never 301/308: a browser must not cache the landing forever."""
    response = _client("/console/").get("/", follow_redirects=False)

    assert response.status_code not in (301, 308)


def test_head_on_root_redirects_too() -> None:
    """Uptime probes and link previewers send HEAD, not GET."""
    response = _client("/console/").head("/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/console/"


def test_redirect_stays_out_of_the_openapi_schema() -> None:
    """The schema describes the API, not the console entry point."""
    app = FastAPI()
    app.include_router(create_root_redirect_router("/console/"))

    assert "/" not in app.openapi().get("paths", {})


def test_root_redirect_is_disabled_by_default() -> None:
    """No deployment gains a redirect it never configured."""
    assert AppConfig(BASELITH_ROOT_REDIRECT="").root_redirect == ""


def test_root_redirect_accepts_a_site_relative_path() -> None:
    """Surrounding whitespace survives a copy-paste into the .env."""
    config = AppConfig(BASELITH_ROOT_REDIRECT="  /console/  ")

    assert config.root_redirect == "/console/"


@pytest.mark.parametrize(
    "target",
    [
        "https://evil.example",  # absolute URL: off-site
        "//evil.example",  # protocol-relative: a host, not a path
        "/\\evil.example",  # browsers normalise the backslash to '/'
        "/land\\ing",  # backslash anywhere
        "/land\r\ning",  # header splitting
        "/",  # redirect loop
        "console/",  # relative: resolves against the request URL
    ],
)
def test_root_redirect_refuses_unsafe_targets(target: str) -> None:
    """A target that could leave the site fails the boot, loudly."""
    with pytest.raises(ValidationError) as excinfo:
        AppConfig(BASELITH_ROOT_REDIRECT=target)

    assert "BASELITH_ROOT_REDIRECT" in str(excinfo.value)
