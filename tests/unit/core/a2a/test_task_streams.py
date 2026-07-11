"""A2A tasks/resubscribe + pushNotification handlers (were method_not_found)."""

import pytest

from core.a2a import AgentCard, EchoA2AServer
from core.a2a.protocol import ErrorCode


def _request(method, params=None, req_id="r1"):
    return {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}


def _send_request():
    return _request(
        "message/send",
        {
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": "Hello!"}],
                "messageId": "msg-1",
            }
        },
    )


async def _seed_task(server):
    """Create a completed task via message/send and return its id."""
    response = await server.dispatch(_send_request())
    return response["result"]["id"]


@pytest.fixture
def server():
    card = AgentCard(name="echo", description="Echo agent for testing")
    return EchoA2AServer(card)


async def test_resubscribe_returns_task_snapshot(server):
    task_id = await _seed_task(server)
    response = await server.dispatch(_request("tasks/resubscribe", {"id": task_id}))
    assert "error" not in response
    assert response["result"]["id"] == task_id


async def test_resubscribe_unknown_task_is_task_not_found(server):
    response = await server.dispatch(_request("tasks/resubscribe", {"id": "ghost"}))
    assert response["error"]["code"] == ErrorCode.TASK_NOT_FOUND


async def test_resubscribe_missing_id_is_invalid_params(server):
    response = await server.dispatch(_request("tasks/resubscribe"))
    assert response["error"]["code"] == ErrorCode.INVALID_PARAMS


async def test_resubscribe_streams_snapshot_then_final(server):
    task_id = await _seed_task(server)
    events = [
        e
        async for e in server.dispatch_stream(
            _request("tasks/resubscribe", {"id": task_id})
        )
    ]
    assert len(events) == 2
    assert events[0]["result"]["id"] == task_id
    assert events[1]["result"]["kind"] == "status-update"
    assert events[1]["result"]["final"] is True


async def test_resubscribe_stream_unknown_task_errors(server):
    events = [
        e
        async for e in server.dispatch_stream(
            _request("tasks/resubscribe", {"id": "ghost"})
        )
    ]
    assert len(events) == 1
    assert events[0]["error"]["code"] == ErrorCode.TASK_NOT_FOUND


@pytest.mark.parametrize(
    "method", ["tasks/pushNotification/set", "tasks/pushNotification/get"]
)
async def test_push_notification_answers_spec_error(server, method):
    response = await server.dispatch(_request(method, {"id": "t1"}))
    # -32007, NOT method_not_found (-32601): the conformant answer for an
    # agent whose card advertises pushNotifications=false.
    assert response["error"]["code"] == ErrorCode.PUSH_NOTIFICATION_NOT_SUPPORTED


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
