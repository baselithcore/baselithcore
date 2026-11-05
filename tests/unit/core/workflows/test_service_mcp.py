"""WorkflowService wiring (scheduler, MCP) and workflows as MCP tools."""

from types import SimpleNamespace

import pytest

from baselith.core.config.workflows import WorkflowsConfig
from baselith.core.mcp.server import MCPServer
from baselith.core.workflows.builder import WorkflowBuilder, WorkflowDefinition
from baselith.core.workflows.mcp_tools import (
    register_workflow_tool,
    tool_name_for,
    unregister_workflow_tool,
)
from baselith.core.workflows.service import (
    WorkflowService,
    get_workflow_service,
    set_workflow_service,
    start_workflow_service,
    stop_workflow_service,
)

pytestmark = [pytest.mark.unit]


def _config(tmp_path=None, **overrides) -> WorkflowsConfig:
    values = {"WORKFLOWS_ENABLED": "true", **overrides}
    if tmp_path is not None:
        values["WORKFLOWS_DIR"] = str(tmp_path)
    return WorkflowsConfig(**values)


def _wf(workflow_id="greet", **fields) -> WorkflowDefinition:
    wf = WorkflowBuilder("g").start().template("t", "hi {{ input }}").end().build()
    wf.id = workflow_id
    for key, value in fields.items():
        setattr(wf, key, value)
    return wf


def _app(server=None):
    return SimpleNamespace(state=SimpleNamespace(mcp_server=server))


class TestStartStop:
    async def test_disabled_by_default_starts_nothing(self, monkeypatch):
        monkeypatch.setattr(
            "baselith.core.workflows.service.get_workflows_config",
            lambda: WorkflowsConfig(),
        )
        app = _app()
        await start_workflow_service(app)
        assert get_workflow_service() is None
        assert not hasattr(app.state, "workflow_service")

    async def test_enabled_loads_the_directory_and_registers_the_service(
        self, tmp_path, monkeypatch
    ):
        (tmp_path / "greet.yaml").write_text(_wf("greet").to_yaml())
        monkeypatch.setattr(
            "baselith.core.workflows.service.get_workflows_config",
            lambda: _config(tmp_path),
        )
        app = _app()
        await start_workflow_service(app)
        try:
            service = get_workflow_service()
            assert service is app.state.workflow_service
            assert service.catalog.get("greet") is not None
        finally:
            await stop_workflow_service(app)
        assert get_workflow_service() is None
        await stop_workflow_service(app)  # idempotent

    def teardown_method(self):
        set_workflow_service(None)


class TestScheduler:
    async def test_catalog_changes_keep_the_schedule_in_step(self):
        service = WorkflowService(_config(WORKFLOWS_SCHEDULER_ENABLED="true"))
        await service.start()
        try:
            assert service.scheduler is not None
            service.catalog.register(_wf("nightly", schedule="0 3 * * *"))
            assert service.scheduler.next_run_at("nightly") is not None
            service.catalog.register(_wf("nightly"))  # schedule removed
            assert service.scheduler.next_run_at("nightly") is None
            service.catalog.register(_wf("hourly", schedule="0 * * * *"))
            service.catalog.remove("hourly")
            assert service.scheduler.next_run_at("hourly") is None
        finally:
            await service.stop()

    async def test_scheduled_runs_go_through_the_runtime(self):
        service = WorkflowService(_config(WORKFLOWS_SCHEDULER_ENABLED="true"))
        await service.start()
        try:
            service.catalog.register(_wf("job", schedule="* * * * *"))
            from datetime import UTC, datetime, timedelta

            results = await service.scheduler.run_due(
                datetime.now(UTC) + timedelta(minutes=2)
            )
            assert [r.workflow_id for r in results] == ["job"]
            history = service.runtime.list_runs("job")
            assert history and history[0].source == "schedule"
        finally:
            await service.stop()

    async def test_scheduler_is_off_unless_asked_for(self):
        service = WorkflowService(_config())
        await service.start()
        try:
            assert service.scheduler is None
        finally:
            await service.stop()

    async def test_stop_cancels_the_scheduler_task(self):
        service = WorkflowService(_config(WORKFLOWS_SCHEDULER_ENABLED="true"))
        await service.start()
        task = service._scheduler_task
        await service.stop()
        assert task.cancelled() or task.done()
        assert service._scheduler_task is None


MCP_ON = {"mcp": {"expose": True, "category": "read_only", "description": "Say hi"}}


class TestMcpTools:
    def test_tool_name(self):
        assert tool_name_for("a.b-c") == "workflow.a.b-c"
        assert tool_name_for("x y") == "workflow.x_y"
        assert len(tool_name_for("x" * 300)) == 128

    async def test_only_opted_in_workflows_become_tools(self):
        server = MCPServer()
        service = WorkflowService(_config())
        service.catalog.register(_wf("shown", metadata=MCP_ON))
        service.catalog.register(_wf("hidden"))
        service.catalog.register(
            _wf("also-hidden", metadata={"mcp": {"expose": "yes"}})
        )
        await service.start(_app(server))
        names = set(server._tools)
        assert names == {"workflow.shown"}
        tool = server._tools["workflow.shown"]
        assert tool.category == "read_only" and tool.description == "Say hi"

    async def test_default_category_is_destructive(self):
        server = MCPServer()
        wf = _wf("w", metadata={"mcp": {"expose": True}})
        runtime = WorkflowService(_config()).runtime
        register_workflow_tool(server, wf, runtime)
        assert server._tools["workflow.w"].category == "destructive"

    async def test_unknown_category_falls_back_to_destructive(self):
        server = MCPServer()
        wf = _wf("w", metadata={"mcp": {"expose": True, "category": "harmless"}})
        register_workflow_tool(server, wf, WorkflowService(_config()).runtime)
        assert server._tools["workflow.w"].category == "destructive"

    async def test_calling_the_tool_runs_the_workflow(self):
        server = MCPServer()
        service = WorkflowService(_config())
        service.catalog.register(_wf("shown", metadata=MCP_ON))
        await service.start(_app(server))
        result = await server._tools["workflow.shown"].handler(input="Ada")
        assert result["status"] == "completed" and result["output"] == "hi Ada"
        assert result["awaiting_approval"] is False
        assert service.runtime.get_run(result["run_id"]).source == "mcp"

    async def test_custom_input_schema_receives_the_argument_object(self):
        server = MCPServer()
        schema = {"type": "object", "properties": {"name": {"type": "string"}}}
        wf = (
            WorkflowBuilder("c")
            .start()
            .template("t", "{{ input.name }}!")
            .end()
            .build()
        )
        wf.id, wf.metadata = "c", {"mcp": {"expose": True, "input_schema": schema}}
        service = WorkflowService(_config())
        service.catalog.register(wf)
        await service.start(_app(server))
        assert server._tools["workflow.c"].input_schema == schema
        result = await server._tools["workflow.c"].handler(name="Bo")
        assert result["output"] == "Bo!"

    async def test_catalog_changes_update_the_tool_list(self):
        server = MCPServer()
        service = WorkflowService(_config())
        await service.start(_app(server))
        service.catalog.register(_wf("late", metadata=MCP_ON))
        assert "workflow.late" in server._tools
        service.catalog.register(_wf("late"))  # opted out on replace
        assert "workflow.late" not in server._tools
        service.catalog.register(_wf("late", metadata=MCP_ON))
        service.catalog.remove("late")
        assert "workflow.late" not in server._tools

    async def test_no_server_means_no_mcp_work(self):
        service = WorkflowService(_config())
        await service.start(_app(None))
        service.catalog.register(_wf("x", metadata=MCP_ON))  # must not raise

    def test_unregister_reports_absence(self):
        assert unregister_workflow_tool(MCPServer(), "nope") is False
