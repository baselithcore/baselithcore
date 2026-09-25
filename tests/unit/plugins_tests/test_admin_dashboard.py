"""End-to-end smoke test of the Basic-auth admin dashboard on the real app.

Three defects combined to make the dashboard unusable out of the box:

* ``GET /admin`` answered 500 — the page was looked up under
  ``plugins/static`` while it ships in ``core/static``;
* the page's data calls (``/status``, ``POST /reindex``) are API-key/Bearer
  routes, so the dashboard's HTTP Basic credentials always got 401 — it now
  calls the Basic-gated ``/admin/status`` and ``/admin/reindex`` companions;
* the same-origin ``POST`` the page sends carries ``Origin: <own origin>``,
  which the CSRF guard rejected with the default empty ``ALLOW_ORIGINS``.

The app is built by ``create_app()`` in a fresh interpreter (like
``test_middleware_order``) so the real middleware stack and router wiring are
exercised; only the indexing service is stubbed so no vector store is needed.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

_REPO_ROOT = str(pathlib.Path(__file__).resolve().parents[3])

_CHILD = r"""
import json
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from core.api.factory import create_app


class _Indexing:
    indexed_count = 3

    async def index_documents(self, incremental=True):
        return SimpleNamespace(new_documents=2)


app = create_app()
client = TestClient(app, raise_server_exceptions=False)
auth = ("dash-admin", "dash-pass-0123456789")
same_origin = {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}
cross_site = {"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"}

with patch(
    "plugins.api_routers.status.get_indexing_service", return_value=_Indexing()
), patch("plugins.api_routers.index.get_indexing_service", return_value=_Indexing()):
    page = client.get("/admin", auth=auth)
    results = {
        "page": [page.status_code, "reindexDocs" in page.text],
        "page_no_auth": client.get("/admin").status_code,
        "status": client.get("/admin/status", auth=auth).json(),
        "status_no_auth": client.get("/admin/status").status_code,
        "api_status_basic": client.get("/status", auth=auth).status_code,
        "reindex": client.post("/admin/reindex", auth=auth, headers=same_origin).json(),
        "reindex_cross_site": client.post(
            "/admin/reindex", auth=auth, headers=cross_site
        ).status_code,
        "reindex_bad_pass": client.post(
            "/admin/reindex", auth=("dash-admin", "wrong"), headers=same_origin
        ).status_code,
    }
print("===ADMIN_BEGIN===")
print(json.dumps(results))
print("===ADMIN_END===")
"""


def _run() -> dict:
    env = os.environ.copy()
    env.update(
        {
            "ADMIN_USER": "dash-admin",
            "ADMIN_PASS": "dash-pass-0123456789",
            "ADMIN_PASS_HASHED": "",
            "ENABLE_FEEDBACK": "true",
            "TRUSTED_HOSTS": '["testserver"]',
            "ALLOW_ORIGINS": "",
            "PYTHONPATH": _REPO_ROOT + os.pathsep + env.get("PYTHONPATH", ""),
        }
    )
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD],
        capture_output=True,
        text=True,
        env=env,
        cwd=_REPO_ROOT,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stderr[-4000:]
    payload = proc.stdout.split("===ADMIN_BEGIN===")[1].split("===ADMIN_END===")[0]
    result: dict = json.loads(payload.strip())
    return result


def test_admin_dashboard_end_to_end() -> None:
    r = _run()

    # The page itself is served (it used to 500 on a missing file).
    assert r["page"] == [200, True]
    assert r["page_no_auth"] == 401

    # The dashboard's data calls work with the same Basic credentials…
    assert r["status"]["status"] == "ok"
    assert "clarification" in r["status"]["metrics"]
    assert r["status_no_auth"] == 401
    assert r["reindex"] == {"status": "ok", "new_files_indexed": 2}
    assert r["reindex_bad_pass"] == 401

    # …while the API-key control plane still refuses Basic credentials, and
    # the unsafe dashboard action stays CSRF-protected.
    assert r["api_status_basic"] == 401
    assert r["reindex_cross_site"] == 403
