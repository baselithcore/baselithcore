"""The ``/metrics`` scrape credential shares the admin lockout, and an empty
``METRICS_PASSWORD`` is "unset", not "match the empty string"."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr

import plugins.api_routers.metrics as metrics_mod
from core.config.security import SecurityConfig


class _Manager:
    """Lockout double: records failures, refuses a locked bucket with 429."""

    def __init__(self, *, locked: set[str] | None = None) -> None:
        self.locked = set(locked or ())
        self.failures: list[str] = []
        self.checks: list[str] = []
        self.config = SimpleNamespace(admin_user="admin")

    async def check_admin_lockout(self, identifier: str) -> None:
        self.checks.append(identifier)
        if identifier in self.locked:
            raise HTTPException(status_code=429, detail="locked")

    def admin_credential_cached(self, password: str) -> bool:
        return False

    def verify_admin_password(self, password: str) -> bool:
        return password == "admin-secret"

    async def record_admin_failure(self, identifier: str) -> None:
        self.failures.append(identifier)

    async def clear_admin_failures(self, identifier: str) -> None:
        pass


def _client(monkeypatch: pytest.MonkeyPatch, manager: _Manager) -> TestClient:
    config = SimpleNamespace(
        metrics_auth_required=True,
        metrics_username="metrics",
        metrics_password=SecretStr("scrape-secret"),
    )
    monkeypatch.setattr(metrics_mod, "get_security_config", lambda: config)
    monkeypatch.setattr(
        "core.middleware.security.get_security_manager", lambda: manager
    )
    monkeypatch.setattr(
        metrics_mod, "_render_metrics", lambda accept: (b"# ok\n", "text/plain")
    )
    app = FastAPI()
    app.include_router(metrics_mod.router)
    return TestClient(app)


def test_scrape_credential_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _Manager()
    client = _client(monkeypatch, manager)
    res = client.get("/metrics", auth=("metrics", "scrape-secret"))
    assert res.status_code == 200
    assert manager.failures == []


def test_locked_source_cannot_confirm_a_correct_scrape_guess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The lockout must run *before* the scrape compare: otherwise a locked
    attacker still learns the password from the 200/429 split."""
    manager = _Manager(locked={"testclient"})
    client = _client(monkeypatch, manager)
    res = client.get("/metrics", auth=("metrics", "scrape-secret"))
    assert res.status_code == 429
    assert manager.checks == ["testclient"]


def test_wrong_scrape_password_counts_against_the_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _Manager()
    client = _client(monkeypatch, manager)
    res = client.get("/metrics", auth=("metrics", "nope"))
    assert res.status_code == 401
    assert manager.failures == ["testclient"]


def test_render_runs_off_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _Manager()
    client = _client(monkeypatch, manager)
    calls: list[Any] = []
    real_to_thread = asyncio.to_thread

    async def _spy(fn: Any, *args: Any, **kwargs: Any) -> Any:
        calls.append(fn)
        return await real_to_thread(fn, *args, **kwargs)

    monkeypatch.setattr(metrics_mod.asyncio, "to_thread", _spy)
    res = client.get("/metrics", auth=("metrics", "scrape-secret"))
    assert res.status_code == 200
    assert calls and calls[0] is metrics_mod._render_metrics


@pytest.mark.parametrize("raw", ["", "   "])
def test_empty_metrics_password_is_unset(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    """``.env.example`` ships ``# METRICS_PASSWORD=``; uncommenting it must
    not open /metrics to ``metrics:`` with an empty password."""
    monkeypatch.setenv("METRICS_PASSWORD", raw)
    config = SecurityConfig(_env_file=None)  # type: ignore[call-arg]
    assert config.metrics_password is None


def test_empty_scrape_password_never_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = SimpleNamespace(
        metrics_auth_required=True,
        metrics_username="metrics",
        metrics_password=SecretStr(""),
    )
    monkeypatch.setattr(metrics_mod, "get_security_config", lambda: config)
    creds = SimpleNamespace(username="metrics", password="")
    assert metrics_mod._is_scrape_credential(creds) is False  # type: ignore[arg-type]
