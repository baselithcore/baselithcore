"""The 'smells like prod' shape (auth enforced, environment undeclared) must
arm the hardened posture globally, not just gate /docs.

Before this, an undeclared APP_ENV/ENVIRONMENT silently relaxed every
production-gated control (unsigned plugins loaded, unsigned A2A accepted, the
A2A SSRF deny stayed off) while only the /docs exposure had a compensating
heuristic in the factory.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.utils import runtime_env


@pytest.fixture(autouse=True)
def _restore_span_sinks():
    """create_app() lets plugins register process-global span sinks; restore
    the registry so this test cannot poison later observability tests."""
    from core.observability import span_sink

    before = list(span_sink._span_sinks)
    yield
    span_sink._span_sinks[:] = before


def _security_stub(auth_required: bool) -> SimpleNamespace:
    return SimpleNamespace(
        allow_origins=[],
        trusted_hosts=[],
        auth_required=auth_required,
        max_request_size_bytes=10 * 1024 * 1024,
    )


def _create_app(monkeypatch, *, auth_required: bool):
    from core.api import factory

    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("DOCS_ENABLED", raising=False)
    monkeypatch.setattr(
        factory, "get_security_config", lambda: _security_stub(auth_required)
    )
    return factory.create_app()


def test_auth_without_declared_env_arms_production_posture(monkeypatch):
    app = _create_app(monkeypatch, auth_required=True)
    assert runtime_env.is_production_env() is True
    assert app.docs_url is None


def test_no_auth_keeps_permissive_default(monkeypatch):
    app = _create_app(monkeypatch, auth_required=False)
    assert runtime_env.is_production_env() is False
    assert app.docs_url == "/docs"
