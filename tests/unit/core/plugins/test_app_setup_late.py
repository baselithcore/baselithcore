"""Runtime enable of a plugin whose boot-time app hook was skipped."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from starlette.responses import PlainTextResponse
from starlette.testclient import TestClient

from core.plugins import Plugin, apply_late_app_hook


async def _spa(scope, receive, send):  # type: ignore[no-untyped-def]
    await PlainTextResponse("spa")(scope, receive, send)


class _MountPlugin(Plugin):
    metadata = SimpleNamespace(name="late-mount")  # type: ignore[assignment]

    def __init__(self) -> None:  # no heavy init
        pass

    @classmethod
    def setup_app_middleware(cls, app):  # type: ignore[no-untyped-def]
        app.mount("/late-mount/ui", _spa)


class _MiddlewarePlugin(Plugin):
    metadata = SimpleNamespace(name="late-mw")  # type: ignore[assignment]

    def __init__(self) -> None:
        pass

    @classmethod
    def setup_app_middleware(cls, app):  # type: ignore[no-untyped-def]
        app.add_middleware(lambda a: a)


class _NoHookPlugin(Plugin):
    metadata = SimpleNamespace(name="late-none")  # type: ignore[assignment]

    def __init__(self) -> None:
        pass


def test_mount_hook_runs_once_on_running_app() -> None:
    app = FastAPI()
    with TestClient(app) as client:
        assert client.get("/late-mount/ui/").status_code == 404
        assert apply_late_app_hook(app, _MountPlugin()) is True
        assert client.get("/late-mount/ui/").text == "spa"
        assert apply_late_app_hook(app, _MountPlugin()) is False


def test_middleware_hook_after_start_degrades_not_raises() -> None:
    app = FastAPI()
    with TestClient(app):
        assert apply_late_app_hook(app, _MiddlewarePlugin()) is False


def test_plugin_without_hook_is_ignored() -> None:
    assert apply_late_app_hook(FastAPI(), _NoHookPlugin()) is False
