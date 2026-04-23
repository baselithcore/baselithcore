"""Tests for TaskReplayStore, /dash/replay routes, and Set-of-Mark module."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugins.baselithbot.plugin import BaselithbotPlugin
from plugins.baselithbot.control.replay import TaskReplayStore
from plugins.baselithbot.api.router import create_router
from plugins.baselithbot.browser.som import SomMark, annotate, clear


def _build_app() -> tuple[FastAPI, BaselithbotPlugin]:
    plugin = BaselithbotPlugin(
        state_dir=tempfile.mkdtemp(prefix="baselithbot-replay-tests-")
    )
    app = FastAPI()
    app.include_router(create_router(plugin), prefix="/baselithbot")
    return app, plugin


class TestTaskReplayStore:
    def test_start_add_finish_list_get(self) -> None:
        store = TaskReplayStore(Path(tempfile.mkdtemp()) / "r.sqlite")
        store.start_run(
            run_id="r1", goal="demo", start_url="https://ex.com", max_steps=5
        )
        store.add_step(
            run_id="r1",
            step_index=1,
            action="navigate",
            reasoning="go to ex",
            current_url="https://ex.com",
            screenshot_b64="AAAA",
            extracted_data={},
        )
        store.add_step(
            run_id="r1",
            step_index=2,
            action="extract",
            reasoning="grab title",
            current_url="https://ex.com",
            screenshot_b64="BBBB",
            extracted_data={"title": "Example"},
        )
        store.finish_run(
            run_id="r1",
            success=True,
            final_url="https://ex.com",
            error=None,
            extracted_data={"title": "Example"},
        )
        listed = store.list_runs()
        assert len(listed) == 1
        assert listed[0]["status"] == "completed"
        assert listed[0]["step_count"] == 2

        detail = store.get_run("r1")
        assert detail is not None
        assert detail["step_count"] == 2
        assert detail["screenshot_steps"] == 2
        assert detail["distinct_url_count"] == 1
        assert detail["first_step_ts"] is not None
        assert detail["last_step_ts"] is not None
        assert len(detail["steps"]) == 2
        assert detail["steps"][1]["action"] == "extract"
        assert detail["steps"][1]["extracted_data"] == {"title": "Example"}
        assert detail["extracted_data"] == {"title": "Example"}

    def test_get_unknown_returns_none(self) -> None:
        store = TaskReplayStore(Path(tempfile.mkdtemp()) / "r.sqlite")
        assert store.get_run("nope") is None

    def test_prune_older_than(self) -> None:
        store = TaskReplayStore(Path(tempfile.mkdtemp()) / "r.sqlite")
        store.start_run(run_id="old", goal="o", start_url=None, max_steps=1)
        store.add_step(
            run_id="old",
            step_index=1,
            action="x",
            reasoning="y",
            current_url="",
            screenshot_b64=None,
            extracted_data={},
        )
        dropped = store.prune_older_than(retention_seconds=-1.0)
        assert dropped == 1
        assert store.get_run("old") is None


class TestReplayRoutes:
    def test_list_and_detail(self) -> None:
        app, plugin = _build_app()
        client = TestClient(app)
        plugin.replay.start_run(run_id="abc", goal="demo", start_url=None, max_steps=3)
        plugin.replay.add_step(
            run_id="abc",
            step_index=1,
            action="navigate",
            reasoning="go",
            current_url="https://x",
            screenshot_b64=None,
            extracted_data={},
        )
        plugin.replay.finish_run(
            run_id="abc",
            success=True,
            final_url="https://x",
            error=None,
            extracted_data={},
        )

        res = client.get("/baselithbot/dash/replay/runs")
        assert res.status_code == 200
        body = res.json()
        assert body["returned"] == 1
        assert body["status_counts"] == {"completed": 1}
        assert body["step_totals"] == 1
        assert body["active_runs"] == 0
        assert body["latest_started_ts"] is not None
        assert body["latest_completed_ts"] is not None
        assert body["retention_days"] == 14
        assert body["path"].endswith("replay.sqlite")
        assert body["runs"][0]["max_steps"] == 3
        assert body["runs"][0]["run_id"] == "abc"

        detail = client.get("/baselithbot/dash/replay/runs/abc")
        assert detail.status_code == 200
        run = detail.json()["run"]
        assert run["run_id"] == "abc"
        assert run["step_count"] == 1
        assert run["screenshot_steps"] == 0
        assert run["distinct_url_count"] == 1
        assert run["first_step_ts"] is not None
        assert run["last_step_ts"] is not None
        assert len(run["steps"]) == 1

    def test_unknown_run_returns_404(self) -> None:
        app, _ = _build_app()
        client = TestClient(app)
        res = client.get("/baselithbot/dash/replay/runs/does-not-exist")
        assert res.status_code == 404


class _FakePage:
    """Duck-typed Page stub for SoM unit tests."""

    def __init__(self, marks: list[dict] | Exception | None = None) -> None:
        self._marks = marks
        self.last_script: str | None = None

    async def evaluate(self, script: str, *args) -> object:  # type: ignore[no-untyped-def]
        self.last_script = script
        if isinstance(self._marks, Exception):
            raise self._marks
        # Clear script is the short one; inject is the long multi-kilobyte one.
        if len(script) < 500:
            return True
        return self._marks or []


class TestSetOfMark:
    @pytest.mark.asyncio
    async def test_annotate_returns_marks(self) -> None:
        page = _FakePage(
            marks=[
                {
                    "index": 0,
                    "tag": "a",
                    "role": None,
                    "text": "Home",
                    "href": "/",
                    "bbox": {"x": 0, "y": 0, "w": 100, "h": 20},
                }
            ]
        )
        marks = await annotate(page, max_marks=5)
        assert len(marks) == 1
        assert isinstance(marks[0], SomMark)
        assert marks[0].tag == "a"
        assert marks[0].text == "Home"

    @pytest.mark.asyncio
    async def test_annotate_swallows_errors(self) -> None:
        page = _FakePage(marks=RuntimeError("boom"))
        marks = await annotate(page)
        assert marks == []

    @pytest.mark.asyncio
    async def test_clear_returns_true(self) -> None:
        page = _FakePage(marks=[])
        assert await clear(page) is True


class TestReplayWiredIntoPlugin:
    def test_plugin_instantiates_replay_store(self) -> None:
        app, plugin = _build_app()
        del app
        # Store file initializes on first call.
        _ = plugin.replay.list_runs()
        assert isinstance(plugin.replay, TaskReplayStore)

    def test_mcp_tools_include_som(self) -> None:
        _, plugin = _build_app()

        async def _drive() -> set[str]:
            await plugin.initialize({})
            try:
                return {t["name"] for t in plugin.get_mcp_tools()}
            finally:
                await plugin.shutdown()

        names = asyncio.run(_drive())
        assert "baselithbot_som_annotate" in names
