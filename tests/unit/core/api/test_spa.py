"""SPAStaticFiles: history-API fallback + entry-point no-cache header.

Regression coverage for a real bug: Starlette's StaticFiles.get_path()
resolves a mount root to the literal path "." (os.path.normpath("") == "."),
not "" — a bare `not path` check misses it, silently skipping the no-cache
header on the actual root request while appearing to work in an isolated
test that (incorrectly) passes path="".
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.testclient import TestClient

from core.api.spa import SPAStaticFiles


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    (tmp_path / "index.html").write_text("<html>spa</html>", encoding="utf-8")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("console.log('hi')", encoding="utf-8")

    app = Starlette(
        routes=[Mount("/app", app=SPAStaticFiles(directory=str(tmp_path), html=True))]
    )
    return TestClient(app)


def test_root_serves_index_with_no_cache(client: TestClient) -> None:
    res = client.get("/app/")
    assert res.status_code == 200
    assert res.headers.get("cache-control") == "no-cache"


def test_deep_link_falls_back_to_index_with_no_cache(client: TestClient) -> None:
    # A client-side route (no file on disk, no extension) is rescued to
    # index.html so the SPA router can take over.
    res = client.get("/app/plugin/does-not-exist")
    assert res.status_code == 200
    assert res.headers.get("cache-control") == "no-cache"


def test_real_asset_is_served_without_no_cache(client: TestClient) -> None:
    res = client.get("/app/assets/app.js")
    assert res.status_code == 200
    assert res.headers.get("cache-control") != "no-cache"


def test_missing_asset_404s_honestly(client: TestClient) -> None:
    # A concrete path with a file extension that doesn't exist must stay a
    # real 404 — masking it as HTML would hide broken asset references.
    res = client.get("/app/assets/does-not-exist.js")
    assert res.status_code == 404
