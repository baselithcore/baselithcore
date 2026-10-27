"""HTTP node: egress screening, credentials, response handling."""

import httpx
import pytest
from pydantic import SecretStr

from baselith.core.workflows import nodes_http
from baselith.core.workflows.builder import (
    NodeType,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
)
from baselith.core.workflows.executor import ExecutionStatus, WorkflowExecutor

pytestmark = [pytest.mark.unit]


def _wf(**config) -> WorkflowDefinition:
    wf = WorkflowDefinition(name="http")
    for node in (
        WorkflowNode(id="start", type=NodeType.START, label="s"),
        WorkflowNode(id="h", type=NodeType.HTTP, label="h", config=config),
        WorkflowNode(id="end", type=NodeType.END, label="e"),
    ):
        wf.add_node(node)
    wf.add_edge(WorkflowEdge(id="1", source_id="start", target_id="h"))
    wf.add_edge(WorkflowEdge(id="2", source_id="h", target_id="end"))
    return wf


@pytest.fixture
def seen(monkeypatch):
    """Skip DNS: pin to the literal URL and capture what is sent."""
    captured: dict = {}
    monkeypatch.setattr(
        nodes_http,
        "resolve_pinned_target",
        lambda url, policy: (url, "api.example.com"),
    )
    return captured


def _executor(seen, handler, **services):
    def respond(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return handler(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    return WorkflowExecutor(services={"http_client": client, **services})


async def test_get_returns_parsed_json(seen):
    ex = _executor(seen, lambda r: httpx.Response(200, json={"ok": True, "items": [1]}))
    result = await ex.execute(_wf(url="https://api.example.com/{{ input }}"), "users")
    assert result.status is ExecutionStatus.COMPLETED
    assert result.output["status"] == 200
    assert result.output["body"] == {"ok": True, "items": [1]}
    request = seen["request"]
    assert str(request.url) == "https://api.example.com/users"
    assert request.headers["host"] == "api.example.com"


async def test_templated_json_body_query_and_headers(seen):
    ex = _executor(seen, lambda r: httpx.Response(201, text="created"))
    wf = _wf(
        url="https://api.example.com/items",
        method="post",
        json={"name": "{{ input }}", "n": 3},
        query={"q": "{{ input }}"},
        headers={"X-Trace": "t-{{ input }}"},
    )
    result = await ex.execute(wf, "widget")
    assert result.output["body"] == "created"
    request = seen["request"]
    assert request.method == "POST"
    assert request.url.params["q"] == "widget"
    assert request.headers["x-trace"] == "t-widget"
    assert b'"name":"widget"' in request.content.replace(b" ", b"")


async def test_reserved_headers_in_a_definition_are_dropped(seen):
    ex = _executor(seen, lambda r: httpx.Response(200, text="x"))
    wf = _wf(
        url="https://api.example.com/",
        headers={"Host": "evil.internal", "Transfer-Encoding": "chunked", "X-Ok": "1"},
    )
    await ex.execute(wf, "x")
    request = seen["request"]
    assert request.headers["host"] == "api.example.com"
    assert "transfer-encoding" not in request.headers
    assert request.headers["x-ok"] == "1"


async def test_credential_comes_from_the_operator_not_the_definition(seen):
    ex = _executor(
        seen,
        lambda r: httpx.Response(200, text="x"),
        http_credentials={"crm": {"Authorization": SecretStr("Bearer s3cret")}},
    )
    await ex.execute(_wf(url="https://api.example.com/", credential="crm"), "x")
    assert seen["request"].headers["authorization"] == "Bearer s3cret"


async def test_unknown_credential_fails_without_sending(seen):
    ex = _executor(seen, lambda r: httpx.Response(200, text="x"))
    result = await ex.execute(
        _wf(url="https://api.example.com/", credential="missing"), "x"
    )
    assert result.status is ExecutionStatus.FAILED
    assert "not configured" in (result.error or "")
    assert "request" not in seen


async def test_error_status_fails_unless_allowed(seen):
    ex = _executor(seen, lambda r: httpx.Response(503, text="down"))
    failed = await ex.execute(_wf(url="https://api.example.com/"), "x")
    assert failed.status is ExecutionStatus.FAILED and "503" in (failed.error or "")

    allowed = await ex.execute(
        _wf(url="https://api.example.com/", allow_error_status=True), "x"
    )
    assert allowed.output["status"] == 503 and allowed.output["ok"] is False


async def test_redirects_are_not_followed(seen):
    ex = _executor(
        seen, lambda r: httpx.Response(302, headers={"location": "http://10.0.0.1/"})
    )
    result = await ex.execute(_wf(url="https://api.example.com/"), "x")
    assert result.status is ExecutionStatus.FAILED and "302" in (result.error or "")


async def test_oversized_response_is_refused(seen):
    ex = _executor(seen, lambda r: httpx.Response(200, content=b"a" * 5000))
    result = await ex.execute(
        _wf(url="https://api.example.com/", max_response_bytes=1000), "x"
    )
    assert result.status is ExecutionStatus.FAILED
    assert "larger than" in (result.error or "")


async def test_bad_json_in_a_json_response_falls_back_to_text(seen):
    ex = _executor(
        seen,
        lambda r: httpx.Response(
            200, content=b"{oops", headers={"content-type": "application/json"}
        ),
    )
    result = await ex.execute(_wf(url="https://api.example.com/"), "x")
    assert result.output["body"] == "{oops"


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/",
        "http://localhost/",
        "http://10.1.2.3/",
        "file:///etc/passwd",
        "ftp://example.com/",
    ],
)
async def test_internal_and_non_http_targets_are_blocked(url):
    """No monkeypatching here: the real egress screen must refuse these."""
    sent = []
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(200))
    )
    result = await WorkflowExecutor(services={"http_client": client}).execute(
        _wf(url=url), "x"
    )
    assert result.status is ExecutionStatus.FAILED
    assert sent == []


async def test_allow_internal_flag_lifts_the_block(monkeypatch):
    from baselith.core.config.workflows import WorkflowsConfig

    monkeypatch.setattr(
        "baselith.core.config.workflows.get_workflows_config",
        lambda: WorkflowsConfig(WORKFLOWS_HTTP_ALLOW_INTERNAL="true"),
    )
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text="local"))
    )
    result = await WorkflowExecutor(services={"http_client": client}).execute(
        _wf(url="http://127.0.0.1:8080/health"), "x"
    )
    assert result.output["body"] == "local"


@pytest.mark.parametrize(
    "config", [{}, {"url": ""}, {"url": "https://a.b/", "method": "TRACE"}]
)
async def test_invalid_config_fails(config):
    result = await WorkflowExecutor().execute(_wf(**config), "x")
    assert result.status is ExecutionStatus.FAILED
