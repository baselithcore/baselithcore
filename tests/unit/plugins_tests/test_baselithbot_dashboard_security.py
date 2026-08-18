"""Unit tests for the Baselithbot dashboard auth, rate limits and SPA mount.

Covers:
    - node pairing token issuance / listing
    - bearer-token guard (missing / wrong / valid / query-param refusal)
    - rate limits on sensitive write endpoints
    - security headers and path-traversal handling on the SPA mount
"""

from __future__ import annotations

import tempfile

from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugins.baselithbot.api.ui_api import create_dashboard_router
from plugins.baselithbot.plugin import BaselithbotPlugin
from plugins.baselithbot.policies import DashboardAuth

from ._baselithbot_dashboard_helpers import _build_app


class TestPairingFlow:
    def test_issue_token_and_list_paired(self) -> None:
        app, plugin = _build_app()
        client = TestClient(app)
        issued = client.post(
            "/baselithbot/dash/nodes/token", json={"platform": "macos"}
        )
        assert issued.status_code == 200
        token = issued.json()["token"]

        # The raw pairing handshake is exercised separately; here we just
        # exercise the listing endpoint and a revoke-404 path.
        listed = client.get("/baselithbot/dash/nodes")
        assert listed.status_code == 200
        assert listed.json()["status"]["pending_tokens"] >= 1

        revoke = client.delete("/baselithbot/dash/nodes/nonexistent")
        assert revoke.status_code == 404
        assert token  # sanity: token is non-empty


class TestDashboardAuthGuard:
    """Auth is enforced when ``DashboardAuth`` is initialized with a token."""

    def _app_with_auth(self, token: str) -> FastAPI:
        plugin = BaselithbotPlugin(
            state_dir=tempfile.mkdtemp(prefix="baselithbot-dashboard-tests-")
        )
        auth = DashboardAuth(token=token)
        app = FastAPI()
        router = create_dashboard_router(plugin, auth=auth)
        app.include_router(router, prefix="/baselithbot")
        return app

    def test_missing_token_is_401(self) -> None:
        client = TestClient(self._app_with_auth("secret"))
        res = client.post("/baselithbot/dash/nodes/token", json={})
        assert res.status_code == 401

    def test_wrong_token_is_403(self) -> None:
        client = TestClient(self._app_with_auth("secret"))
        res = client.post(
            "/baselithbot/dash/nodes/token",
            json={},
            headers={"Authorization": "Bearer nope"},
        )
        assert res.status_code == 403

    def test_correct_token_is_accepted(self) -> None:
        client = TestClient(self._app_with_auth("secret"))
        res = client.post(
            "/baselithbot/dash/nodes/token",
            json={"platform": "ios"},
            headers={"Authorization": "Bearer secret"},
        )
        assert res.status_code == 200

    def test_query_param_token_is_rejected(self) -> None:
        """Query-param ``?token=`` must be refused to prevent log/referer leaks."""
        client = TestClient(self._app_with_auth("secret"))
        res = client.post("/baselithbot/dash/nodes/token?token=secret", json={})
        assert res.status_code == 401

    def test_read_endpoints_are_gated_too(self) -> None:
        """Reads return screenshots/transcripts/audit data — as sensitive as
        writes, so they now require the bearer token as well."""
        client = TestClient(self._app_with_auth("secret"))
        res = client.get("/baselithbot/dash/overview")
        assert res.status_code == 401
        res = client.get(
            "/baselithbot/dash/overview",
            headers={"Authorization": "Bearer secret"},
        )
        assert res.status_code == 200

    def test_sse_stream_uses_single_use_ticket(self) -> None:
        """EventSource cannot send headers: the stream accepts a short-lived
        single-use ticket minted by an authenticated call — never the raw
        token in the query string (it would land in access logs)."""
        client = TestClient(self._app_with_auth("secret"))
        sse_headers = {"Accept": "text/event-stream"}

        # Raw token in the query is refused even for SSE.
        res = client.get(
            "/baselithbot/dash/events/recent",  # any gated read w/o header
            params={"token": "secret"},
        )
        assert res.status_code == 401

        # Minting requires auth.
        assert client.post("/baselithbot/dash/events/ticket").status_code == 401
        minted = client.post(
            "/baselithbot/dash/events/ticket",
            headers={"Authorization": "Bearer secret"},
        )
        assert minted.status_code == 200
        ticket = minted.json()["ticket"]

        # The ticket authorizes an SSE request... (use /events/recent with the
        # SSE Accept header to avoid holding a real stream open in tests)
        res = client.get(
            "/baselithbot/dash/events/recent",
            params={"ticket": ticket},
            headers=sse_headers,
        )
        assert res.status_code == 200

        # ...exactly once.
        res = client.get(
            "/baselithbot/dash/events/recent",
            params={"ticket": ticket},
            headers=sse_headers,
        )
        assert res.status_code == 401


class TestRateLimit:
    def test_pairing_token_rate_limit(self) -> None:
        app, _ = _build_app()
        client = TestClient(app)
        # 5 allowed per minute (see _TOKEN_RATE_LIMIT); 6th must 429.
        for _ in range(5):
            assert (
                client.post("/baselithbot/dash/nodes/token", json={}).status_code == 200
            )
        res = client.post("/baselithbot/dash/nodes/token", json={})
        assert res.status_code == 429


class TestUiMount:
    def test_root_redirects_to_ui(self) -> None:
        app, _ = _build_app()
        client = TestClient(app, follow_redirects=False)
        res = client.get("/baselithbot/")
        assert res.status_code in (307, 308)
        assert res.headers["location"] == "/baselithbot/ui/"

    def test_ui_index_serves_and_sets_security_headers(self) -> None:
        app, _ = _build_app()
        client = TestClient(app)
        res = client.get("/baselithbot/ui/")
        # Either the built index or the fallback — both carry the headers.
        assert res.status_code in (200, 503)
        assert res.headers.get("X-Content-Type-Options") == "nosniff"
        assert res.headers.get("X-Frame-Options") == "DENY"
        assert res.headers.get("Referrer-Policy") == "no-referrer"

    def test_ui_path_traversal_is_rejected(self) -> None:
        app, _ = _build_app()
        client = TestClient(app)
        res = client.get("/baselithbot/ui/../../etc/passwd")
        # Either the SPA fallback serves the index or a 404 — never a leak.
        assert res.status_code in (200, 404, 503)
        if res.status_code == 200:
            assert b"passwd" not in res.content.lower()
