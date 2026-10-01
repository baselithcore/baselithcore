"""The audited action path and its agent/MCP tool bridges."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pytest
from pydantic import SecretStr

from core.connectors import (
    ActionResult,
    ActionSpec,
    BaseConnector,
    ConnectorCapability,
    ConnectorConfigError,
    ConnectorSpec,
    ConnectorTransientError,
    CredentialField,
    connector_mcp_tools,
    connector_tool_definitions,
    invoke_action,
)
from core.connectors import tools as tools_module
from core.context import (
    reset_tenant_context,
    reset_user_context,
    set_tenant_context,
    set_user_context,
)
from core.observability.audit import AuditEventType

CREATE = ActionSpec(
    "create_ticket",
    "Open a ticket",
    input_schema={"type": "object", "properties": {"title": {"type": "string"}}},
    category="external_side_effect",
)
PING = ActionSpec("ping", "Ping", category="read_only")


class Tracker(BaseConnector):
    spec = ConnectorSpec(
        name="tracker",
        display_name="Tracker",
        capabilities=frozenset({ConnectorCapability.ACTION}),
        actions=(CREATE, PING),
    )

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.calls: list[tuple[str, dict]] = []
        self.fail: Exception | None = None

    async def invoke(self, action: str, params: Mapping[str, Any]) -> ActionResult:
        self.calls.append((action, dict(params)))
        if self.fail:
            raise self.fail
        if params.get("title") == "":
            return ActionResult(ok=False, error="title required")
        return ActionResult(ok=True, data={"id": "T-1"})


class FakeAudit:
    enabled = True

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def log(self, event_type, **kwargs):
        self.events.append({"event_type": event_type, **kwargs})


@pytest.fixture
def audit(monkeypatch) -> FakeAudit:
    fake = FakeAudit()
    monkeypatch.setattr(tools_module, "get_audit_logger", lambda: fake)
    return fake


@pytest.fixture
def actor():
    tenant = set_tenant_context("tenant-a")
    user = set_user_context("user-1")
    yield
    reset_user_context(user)
    reset_tenant_context(tenant)


async def test_invoke_action_runs_and_audits_with_the_actor(audit, actor):
    tracker = Tracker()
    result = await invoke_action(tracker, "create_ticket", {"title": "secret title"})
    assert result.ok and result.data == {"id": "T-1"}
    [event] = audit.events
    assert event["event_type"] is AuditEventType.TOOL_INVOKE
    assert event["resource"] == "connector:tracker"
    assert event["action"] == "create_ticket"
    assert event["user_id"] == "user-1"
    assert event["tenant_id"] == "tenant-a"
    assert event["success"] is True
    assert "secret title" not in json.dumps(event, default=str)


async def test_business_refusal_is_audited_as_unsuccessful(audit):
    result = await invoke_action(Tracker(), "create_ticket", {"title": ""})
    assert not result.ok
    assert audit.events[0]["success"] is False
    assert audit.events[0]["details"]["error"] == "title required"


async def test_undeclared_action_is_refused_before_invoking(audit):
    tracker = Tracker()
    with pytest.raises(ConnectorConfigError, match="delete_all"):
        await invoke_action(tracker, "delete_all", {})
    assert tracker.calls == []


async def test_connector_errors_are_audited_and_reraised(audit):
    tracker = Tracker()
    tracker.fail = ConnectorTransientError("tracker", "HTTP 503")
    with pytest.raises(ConnectorTransientError):
        await invoke_action(tracker, "ping", {})
    assert audit.events[0]["success"] is False
    assert "503" in audit.events[0]["details"]["error"]


async def test_non_action_connector_is_refused(audit):
    class Reader(BaseConnector):
        spec = ConnectorSpec(name="reader", display_name="Reader")

    with pytest.raises(ConnectorConfigError):
        await invoke_action(Reader(), "ping", {})


async def test_tool_definitions_mirror_the_declared_actions(audit):
    tracker = Tracker()
    tools = {t.name: t for t in connector_tool_definitions(tracker)}
    assert set(tools) == {"tracker.create_ticket", "tracker.ping"}
    create = tools["tracker.create_ticket"]
    assert create.category == "external_side_effect"
    assert create.parameters == CREATE.input_schema
    assert tools["tracker.ping"].category == "read_only"
    out = await create.fn(title="hello")
    assert json.loads(out) == {"ok": True, "data": {"id": "T-1"}, "error": None}
    assert tracker.calls == [("create_ticket", {"title": "hello"})]
    assert len(audit.events) == 1


async def test_mcp_tools_use_the_plugin_hook_shape(audit):
    tracker = Tracker()
    [create, ping] = connector_mcp_tools(tracker)
    assert create["name"] == "tracker.create_ticket"
    assert create["input_schema"] == CREATE.input_schema
    assert create["category"] == "external_side_effect"
    assert ping["input_schema"] == {"type": "object", "properties": {}}
    result = await ping["handler"]()
    assert json.loads(result)["ok"] is True


async def test_connector_written_refusals_are_redacted_in_the_audit(audit):
    class Leaky(Tracker):
        spec = Tracker.spec.__class__(
            name="leaky",
            display_name="Leaky",
            capabilities=Tracker.spec.capabilities,
            credentials=(CredentialField("api_key"),),
            actions=Tracker.spec.actions,
        )

        async def invoke(self, action, params):
            return ActionResult(
                ok=False, error=f"vendor says: bad key {self.credential('api_key')}"
            )

    leaky = Leaky(credentials={"api_key": SecretStr("sk-live-123")})
    result = await invoke_action(leaky, "ping", {})
    assert not result.ok
    assert "sk-live-123" not in json.dumps(audit.events, default=str)


async def test_unexpected_exceptions_are_audited_without_their_message(audit):
    tracker = Tracker()
    tracker.fail = KeyError("sk-live-123")
    with pytest.raises(KeyError):
        await invoke_action(tracker, "create_ticket", {"title": "x"})
    [event] = audit.events
    assert event["success"] is False
    assert event["details"]["error"] == "KeyError"
