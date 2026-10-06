"""Tests for the runs / approvals / webhooks surface, via httpx.MockTransport."""

import base64
import json

import httpx
import pytest
from baselith_sdk import (
    AsyncBaselithClient,
    BaselithClient,
    NotFoundError,
    RunTimeoutError,
    iter_pages,
)
from baselith_sdk._sse import _iter_run_events
from baselith_sdk.errors import BaselithConfigError

BASE = "https://api.test"


class Recorder:
    """A MockTransport handler that records requests and replays responses."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        resp = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        return resp

    @property
    def last(self) -> httpx.Request:
        return self.requests[-1]

    def body(self, i=-1):
        return json.loads(self.requests[i].content)


def ok(payload, status=200, headers=None):
    return httpx.Response(status, json=payload, headers=headers or {})


def sync(rec, **kw):
    return BaselithClient(
        BASE, api_key="k", max_retries=0, transport=httpx.MockTransport(rec), **kw
    )


def aclient(rec, **kw):
    return AsyncBaselithClient(
        BASE, api_key="k", max_retries=0, transport=httpx.MockTransport(rec), **kw
    )


def page(key, items, next_cursor=None):
    return {
        key: items,
        "count": len(items),
        "next_cursor": next_cursor,
        "has_more": next_cursor is not None,
    }


# === Auth ===
def test_basic_auth_header():
    rec = Recorder(ok(page("pending", [])))
    with sync(rec, basic_auth=("admin", "s3cret")) as c:
        c.list_approvals()
    token = base64.b64encode(b"admin:s3cret").decode()
    assert rec.last.headers["authorization"] == f"Basic {token}"


def test_basic_and_bearer_conflict():
    with pytest.raises(BaselithConfigError):
        BaselithClient(BASE, bearer_token="t", basic_auth=("a", "b"))


# === Async agent runs ===
def test_submit_agent_run():
    rec = Recorder(
        ok(
            {"task_id": "t1", "status_url": "/v1/agent/status/t1"},
            status=202,
            headers={"Location": "/v1/agent/status/t1"},
        )
    )
    with sync(rec) as c:
        sub = c.submit_agent_run("hello", conversation_id="c1")
    assert rec.last.method == "POST"
    assert str(rec.last.url) == f"{BASE}/v1/agent/async"
    assert rec.body() == {"query": "hello", "conversation_id": "c1"}
    assert rec.last.headers["idempotency-key"]
    assert (sub.task_id, sub.status_url) == ("t1", "/v1/agent/status/t1")
    assert sub.location == "/v1/agent/status/t1"


def test_get_agent_run_encodes_path_param():
    rec = Recorder(ok({"status": "running", "progress": 0.5}))
    with sync(rec) as c:
        st = c.get_agent_run("a/b?c")
    assert rec.last.url.raw_path == b"/v1/agent/status/a%2Fb%3Fc"
    assert st.status == "running" and not st.is_terminal


def test_wait_for_run_polls_until_terminal(monkeypatch):
    import baselith_sdk._pagination as pag

    monkeypatch.setattr(pag.time, "sleep", lambda *_: None)
    rec = Recorder(
        ok({"status": "queued"}),
        ok({"status": "running"}),
        ok({"status": "completed", "result": {"answer": "42"}}),
    )
    with sync(rec) as c:
        st = c.wait_for_run("t1", timeout=10, poll_interval=0.01)
    assert len(rec.requests) == 3
    assert st.status == "completed" and st.result == {"answer": "42"}


def test_wait_for_run_times_out():
    rec = Recorder(ok({"status": "running"}))
    with sync(rec) as c, pytest.raises(RunTimeoutError) as ei:
        c.wait_for_run("t1", timeout=0, poll_interval=0.01)
    assert ei.value.task_id == "t1"
    assert ei.value.last_status.status == "running"


def test_wait_for_run_rejects_bad_interval():
    with sync(Recorder(ok({}))) as c, pytest.raises(BaselithConfigError):
        c.wait_for_run("t1", poll_interval=0)


def test_problem_json_error_on_new_route():
    rec = Recorder(
        httpx.Response(
            404,
            json={
                "type": "urn:baselith:error:not_found",
                "title": "Not Found",
                "status": 404,
                "detail": "unknown task id",
                "code": "not_found",
                "request_id": "req-9",
            },
            headers={"content-type": "application/problem+json"},
        )
    )
    with sync(rec) as c, pytest.raises(NotFoundError) as ei:
        c.get_agent_run("nope")
    assert ei.value.code == "not_found"
    assert ei.value.request_id == "req-9"
    assert ei.value.message == "unknown task id"


# === Run events (SSE) ===
_EVENTS = (
    ": keepalive\n\n"
    'id: e1\nevent: run_started\ndata: {"type": "run_started", "agent_id": "a"}\n\n'
    ": keepalive\n\n"
    'id: e2\nevent: thought\ndata: {"type": "thought", "content": "hmm"}\n\n'
    'id: e3\nevent: final\ndata: {"type": "final", "content": "done"}\n\n'
    'id: e4\nevent: thought\ndata: {"type": "thought", "content": "late"}\n\n'
)


def test_stream_run_events_parses_ids_and_stops_at_terminal():
    rec = Recorder(
        httpx.Response(200, text=_EVENTS, headers={"content-type": "text/event-stream"})
    )
    with sync(rec) as c:
        events = list(c.stream_run_events("r1", last_event_id="e0"))
    assert str(rec.last.url) == f"{BASE}/v1/runs/r1/events"
    assert rec.last.method == "GET"
    assert rec.last.headers["accept"] == "text/event-stream"
    assert rec.last.headers["last-event-id"] == "e0"
    assert [e.id for e in events] == ["e1", "e2", "e3"]
    assert [e.type for e in events] == ["run_started", "thought", "final"]
    assert events[1].content == "hmm" and events[-1].is_terminal


def test_run_error_event_is_yielded_not_raised():
    raw = ["id: x\nevent: err", 'or\ndata: {"type": "error", "content": "boom"}\n\n']
    events = list(_iter_run_events(iter(raw)))
    assert len(events) == 1
    assert events[0].type == "error" and events[0].content == "boom"
    assert events[0].is_terminal


def test_stream_run_events_http_error():
    rec = Recorder(httpx.Response(401, json={"detail": "nope"}))
    with sync(rec) as c, pytest.raises(Exception) as ei:
        list(c.stream_run_events("r1"))
    assert getattr(ei.value, "status_code", None) == 401


# === Run history ===
def test_get_run_history_passes_cursor():
    rec = Recorder(ok({"run_id": "r1", **page("history", [{"version": 1}], "cur2")}))
    with sync(rec) as c:
        hp = c.get_run_history("r1", limit=5, cursor="cur1")
    assert rec.last.url.path == "/v1/runs/r1/history"
    assert dict(rec.last.url.params) == {"limit": "5", "cursor": "cur1"}
    assert hp.items == [{"version": 1}] and hp.next_cursor == "cur2"


# === Approvals ===
def test_list_approvals_omits_none_params():
    rec = Recorder(ok(page("pending", [{"run_id": "r1", "updated_at": 1.0}])))
    with sync(rec) as c:
        ap = c.list_approvals(tenant_id="acme")
    assert dict(rec.last.url.params) == {"tenant_id": "acme"}
    assert ap.items[0].run_id == "r1" and not ap.has_more


def test_decide_approval():
    rec = Recorder(ok({"run_id": "r1", "recorded": True, "approved": False}))
    with sync(rec) as c:
        res = c.decide_approval("r1", False, reason="too risky")
    assert str(rec.last.url) == f"{BASE}/v1/approvals/r1/decision"
    assert rec.body() == {"approved": False, "reason": "too risky"}
    assert res.recorded and not res.approved


def test_resume_run():
    rec = Recorder(ok({"run_id": "r1", "result": {"answer": "ok"}}))
    with sync(rec) as c:
        res = c.resume_run("r1")
    assert rec.last.method == "POST"
    assert rec.last.url.path == "/v1/approvals/r1/resume"
    assert res.result == {"answer": "ok"}


# === Webhooks ===
_ENDPOINT = {
    "id": "whe_1",
    "url": "https://hooks.example.com/x",
    "event_types": ["*"],
    "enabled": True,
    "has_secret": True,
}
_DELIVERY = {
    "id": "whd_1",
    "endpoint_id": "whe_1",
    "event_id": "evt_1",
    "event_type": "agent.completed",
    "status": "failed",
    "attempts": 3,
}


def test_create_webhook():
    rec = Recorder(ok({"endpoint": _ENDPOINT, "secret": "whsec_x"}, status=201))
    with sync(rec) as c:
        created = c.create_webhook(
            "https://hooks.example.com/x", event_types=["agent.completed"]
        )
    assert rec.last.method == "POST" and rec.last.url.path == "/v1/webhooks"
    assert rec.body() == {
        "url": "https://hooks.example.com/x",
        "event_types": ["agent.completed"],
    }
    assert rec.last.headers["idempotency-key"]
    assert created.secret == "whsec_x" and created.endpoint.id == "whe_1"


def test_create_webhook_defaults_to_all_events():
    rec = Recorder(ok({"endpoint": _ENDPOINT, "secret": "s"}, status=201))
    with sync(rec) as c:
        c.create_webhook("https://hooks.example.com/x")
    assert rec.body()["event_types"] == ["*"]


def test_list_and_delete_webhooks():
    rec = Recorder(
        ok(page("endpoints", [_ENDPOINT])),
        ok({"status": "deleted", "endpoint_id": "whe_1"}),
    )
    with sync(rec) as c:
        wp = c.list_webhooks(limit=10)
        deleted = c.delete_webhook("whe_1")
    assert wp.items[0].url == _ENDPOINT["url"]
    assert rec.requests[0].url.params["limit"] == "10"
    assert rec.last.method == "DELETE" and rec.last.url.path == "/v1/webhooks/whe_1"
    assert deleted["status"] == "deleted"


def test_deliveries_list_and_replay():
    rec = Recorder(
        ok(page("deliveries", [_DELIVERY], "n1")),
        ok({"status": "success", "delivery": {**_DELIVERY, "status": "success"}}),
    )
    with sync(rec) as c:
        dp = c.list_webhook_deliveries()
        rep = c.replay_webhook_delivery("whd_1")
    assert rec.requests[0].url.path == "/v1/webhooks/deliveries"
    assert dp.items[0].attempts == 3 and dp.has_more
    assert rec.last.url.path == "/v1/webhooks/deliveries/whd_1/replay"
    assert rep.status == "success" and rep.delivery.status == "success"


# === Pagination ===
def test_iter_pages_follows_next_cursor():
    rec = Recorder(
        ok(page("endpoints", [_ENDPOINT], "c2")),
        ok(page("endpoints", [{**_ENDPOINT, "id": "whe_2"}], "c3")),
        ok(page("endpoints", [{**_ENDPOINT, "id": "whe_3"}])),
    )
    with sync(rec) as c:
        ids = [e.id for p in c.iter_pages(c.list_webhooks, limit=1) for e in p.items]
    assert ids == ["whe_1", "whe_2", "whe_3"]
    assert [r.url.params.get("cursor") for r in rec.requests] == [None, "c2", "c3"]
    assert all(r.url.params["limit"] == "1" for r in rec.requests)


def test_iter_pages_module_function_with_extra_args():
    rec = Recorder(ok({"run_id": "r1", **page("history", [{"version": 1}])}))
    with sync(rec) as c:
        pages = list(iter_pages(c.get_run_history, "r1"))
    assert len(pages) == 1 and pages[0].run_id == "r1"


def test_iter_pages_stops_on_repeated_cursor():
    rec = Recorder(ok(page("deliveries", [_DELIVERY], "same")))
    with sync(rec) as c:
        pages = list(c.iter_pages(c.list_webhook_deliveries, cursor="same"))
    assert len(pages) == 1


# === Async client ===
async def test_async_agent_run_roundtrip(monkeypatch):
    import baselith_sdk._pagination as pag

    async def no_sleep(*_):
        return None

    monkeypatch.setattr(pag.asyncio, "sleep", no_sleep)
    rec = Recorder(
        ok({"task_id": "t1", "status_url": "/v1/agent/status/t1"}, status=202),
        ok({"status": "running"}),
        ok({"status": "failed", "error": "boom"}),
    )
    async with aclient(rec) as c:
        sub = await c.submit_agent_run("q")
        st = await c.wait_for_run(sub.task_id, timeout=5, poll_interval=0.01)
    assert st.status == "failed" and st.error == "boom"
    assert rec.requests[1].url.path == "/v1/agent/status/t1"


async def test_async_wait_for_run_times_out():
    rec = Recorder(ok({"status": "queued"}))
    async with aclient(rec) as c:
        with pytest.raises(RunTimeoutError):
            await c.wait_for_run("t1", timeout=0, poll_interval=0.01)


async def test_async_stream_run_events():
    rec = Recorder(httpx.Response(200, text=_EVENTS))
    async with aclient(rec) as c:
        events = [e async for e in c.stream_run_events("r1")]
    assert [e.id for e in events] == ["e1", "e2", "e3"]
    assert "last-event-id" not in rec.last.headers


async def test_async_history_and_approvals():
    rec = Recorder(
        ok({"run_id": "r1", **page("history", [])}),
        ok(page("pending", [])),
        ok({"run_id": "r1", "recorded": True, "approved": True}),
        ok({"run_id": "r1", "result": None}),
    )
    async with aclient(rec, basic_auth=("admin", "pw")) as c:
        hp = await c.get_run_history("r1")
        ap = await c.list_approvals(limit=2)
        dec = await c.decide_approval("r1", True, approver="alice")
        res = await c.resume_run("r1")
    assert hp.items == [] and ap.items == []
    assert rec.body(2) == {"approved": True, "approver": "alice"}
    assert dec.approved and res.run_id == "r1"
    assert rec.requests[3].url.path == "/v1/approvals/r1/resume"
    assert rec.requests[0].headers["authorization"].startswith("Basic ")


async def test_async_webhooks_and_iter_pages():
    rec = Recorder(
        ok({"endpoint": _ENDPOINT, "secret": "s"}, status=201),
        ok(page("endpoints", [_ENDPOINT], "c2")),
        ok(page("endpoints", [{**_ENDPOINT, "id": "whe_2"}])),
        ok({"status": "deleted", "endpoint_id": "whe_1"}),
        ok(page("deliveries", [_DELIVERY])),
        ok({"status": "success", "delivery": _DELIVERY}),
    )
    async with aclient(rec) as c:
        created = await c.create_webhook("https://hooks.example.com/x")
        ids = [e.id async for p in c.iter_pages(c.list_webhooks) for e in p.items]
        deleted = await c.delete_webhook("whe_1")
        dp = await c.list_webhook_deliveries(limit=3)
        rep = await c.replay_webhook_delivery("whd_1")
    assert created.endpoint.id == "whe_1"
    assert ids == ["whe_1", "whe_2"]
    assert rec.requests[2].url.params["cursor"] == "c2"
    assert deleted["endpoint_id"] == "whe_1"
    assert dp.items[0].id == "whd_1" and rec.requests[4].url.params["limit"] == "3"
    assert rep.delivery.endpoint_id == "whe_1"


async def test_async_problem_json_error():
    rec = Recorder(
        httpx.Response(
            404,
            json={"title": "Not Found", "code": "not_found", "request_id": "r-1"},
            headers={"content-type": "application/problem+json"},
        )
    )
    async with aclient(rec) as c:
        with pytest.raises(NotFoundError) as ei:
            await c.delete_webhook("missing")
    assert ei.value.code == "not_found" and ei.value.request_id == "r-1"
