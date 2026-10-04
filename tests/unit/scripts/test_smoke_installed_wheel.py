"""The installed-wheel smoke test must fail loudly, never pass vacuously.

`scripts/smoke_installed_wheel.py` exists because a green check against the
checkout said nothing about the wheel. Each case pins one way it could go green
without testing the install: importing the checkout, accepting a 503 or a
readiness body with the database down, waiting on a server that already died.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from scripts import smoke_installed_wheel as smoke

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_an_import_from_the_checkout_is_rejected() -> None:
    with pytest.raises(smoke.SmokeError, match="inside the checkout"):
        smoke.assert_outside(REPO_ROOT / "baselith" / "__init__.py", REPO_ROOT)


def test_an_import_from_site_packages_is_accepted(tmp_path: Path) -> None:
    smoke.assert_outside(tmp_path / "site-packages" / "baselith" / "__init__.py")


def test_check_import_refuses_the_source_tree() -> None:
    """The unit run imports the checkout — exactly what the check must catch."""
    with pytest.raises(smoke.SmokeError, match="inside the checkout"):
        smoke.check_import(REPO_ROOT)


def test_check_import_binds_every_public_name(tmp_path: Path) -> None:
    """With the location check pointed elsewhere, the star import must hold."""
    assert smoke.check_import(tmp_path).endswith("__init__.py")


def test_wait_for_retries_until_200() -> None:
    answers = iter([OSError("refused"), (503, b"starting"), (200, b"ok")])

    def get(_url: str) -> tuple[int, bytes]:
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer

    assert smoke.wait_for("u", 5, get=get, interval=0) == (200, b"ok")


def test_wait_for_returns_the_last_answer_on_timeout() -> None:
    status, body = smoke.wait_for(
        "u", 0.05, get=lambda _u: (503, b"down"), interval=0.01
    )
    assert (status, body) == (503, b"down")


def test_a_dead_server_fails_fast() -> None:
    with pytest.raises(smoke.SmokeError, match="exited"):
        smoke.wait_for("u", 30, get=lambda _u: (0, b""), alive=lambda: False)


def _ready(**services: bool) -> bytes:
    return json.dumps({"status": "ready", "services": services}).encode()


def test_ready_requires_200() -> None:
    with pytest.raises(smoke.SmokeError, match="503"):
        smoke.assert_ready(503, _ready(database=False), require_redis=False)


def test_ready_requires_the_database_reported_up() -> None:
    with pytest.raises(smoke.SmokeError, match="database"):
        smoke.assert_ready(200, _ready(redis=True), require_redis=False)


def test_redis_is_checked_only_on_request() -> None:
    smoke.assert_ready(200, _ready(database=True, redis=False), require_redis=False)
    with pytest.raises(smoke.SmokeError, match="redis"):
        smoke.assert_ready(200, _ready(database=True, redis=False), require_redis=True)


def test_unknown_checks_are_rejected() -> None:
    with pytest.raises(SystemExit):
        smoke.parse_args(["--checks", "import,deploy"])


def test_main_leaves_the_checkout_before_running(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[Path] = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "path", [str(REPO_ROOT / "scripts"), *sys.path])
    monkeypatch.setattr(smoke, "check_cli", lambda: seen.append(Path.cwd()))
    assert smoke.main(["--checks", "cli"]) == 0
    assert seen and REPO_ROOT not in seen[0].resolve().parents
    assert str(REPO_ROOT / "scripts") not in sys.path


def test_main_reports_the_first_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "path", list(sys.path))

    def broken() -> None:
        raise smoke.SmokeError("boom")

    monkeypatch.setattr(smoke, "check_cli", broken)
    assert smoke.main(["--checks", "cli"]) == 1
