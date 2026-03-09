from core.a2a.protocol import (
    ErrorCode,
    JSONRPCError,
    JSONRPCRequest,
    JSONRPCResponse,
    A2AMessage,
    MessageType,
    A2AMethod,
    A2ARequest,
    A2AResponse,
)


def test_jsonrpc_error_to_dict():
    error = JSONRPCError(
        code=ErrorCode.PARSE_ERROR,
        message="Parse error",
        data={"detail": "invalid json"},
    )
    d = error.to_dict()
    assert d["code"] == ErrorCode.PARSE_ERROR
    assert d["message"] == "Parse error"
    assert d["data"] == {"detail": "invalid json"}


def test_jsonrpc_error_from_dict():
    data = {"code": -32600, "message": "Invalid Request", "data": "more info"}
    error = JSONRPCError.from_dict(data)
    assert error.code == -32600
    assert error.message == "Invalid Request"
    assert error.data == "more info"


def test_jsonrpc_error_factories():
    assert JSONRPCError.parse_error().code == ErrorCode.PARSE_ERROR
    assert JSONRPCError.invalid_request().code == ErrorCode.INVALID_REQUEST
    assert JSONRPCError.method_not_found("test").code == ErrorCode.METHOD_NOT_FOUND
    assert "test" in JSONRPCError.method_not_found("test").message
    assert JSONRPCError.invalid_params().code == ErrorCode.INVALID_PARAMS
    assert JSONRPCError.internal_error().code == ErrorCode.INTERNAL_ERROR
    assert JSONRPCError.task_not_found("task-123").code == ErrorCode.TASK_NOT_FOUND


def test_jsonrpc_request_to_dict():
    request = JSONRPCRequest(method="test/method", params={"key": "val"}, id="1")
    d = request.to_dict()
    assert d["jsonrpc"] == "2.0"
    assert d["method"] == "test/method"
    assert d["id"] == "1"
    assert d["params"] == {"key": "val"}


def test_jsonrpc_request_from_dict():
    data = {"jsonrpc": "2.0", "method": "test", "id": "uuid-123", "params": {"x": 1}}
    req = JSONRPCRequest.from_dict(data)
    assert req.method == "test"
    assert req.id == "uuid-123"
    assert req.params == {"x": 1}


def test_jsonrpc_request_factories():
    req1 = JSONRPCRequest.message_send(
        {"text": "hi"}, context_id="ctx-1", metadata={"priority": "high"}
    )
    assert req1.method == A2AMethod.MESSAGE_SEND.value
    assert req1.params["message"] == {"text": "hi"}
    assert req1.params["contextId"] == "ctx-1"
    assert req1.params["metadata"] == {"priority": "high"}

    req2 = JSONRPCRequest.tasks_get("task-1", history_length=10)
    assert req2.method == A2AMethod.TASKS_GET.value
    assert req2.params["id"] == "task-1"
    assert req2.params["historyLength"] == 10

    req3 = JSONRPCRequest.tasks_cancel("task-1")
    assert req3.method == A2AMethod.TASKS_CANCEL.value
    assert req3.params["id"] == "task-1"


def test_jsonrpc_response():
    # Success
    resp = JSONRPCResponse.success(request_id="1", result={"ok": True})
    assert resp.is_success
    d = resp.to_dict()
    assert d["result"] == {"ok": True}
    assert d["id"] == "1"

    # Failure
    err = JSONRPCError.internal_error()
    resp_err = JSONRPCResponse.failure(request_id="1", error=err)
    assert not resp_err.is_success
    d_err = resp_err.to_dict()
    assert "error" in d_err
    assert d_err["error"]["code"] == ErrorCode.INTERNAL_ERROR

    # From dict
    resp_from = JSONRPCResponse.from_dict(d_err)
    assert resp_from.id == "1"
    assert resp_from.error.code == ErrorCode.INTERNAL_ERROR


def test_a2a_message_legacy():
    msg = A2AMessage(
        type=MessageType.REQUEST,
        method="test",
        params={"foo": "bar"},
        from_agent="a",
        to_agent="b",
        result={"some": "res"},
        error={"err": "msg"},
    )
    d = msg.to_dict()
    assert d["type"] == "request"
    assert d["method"] == "test"
    assert d["params"] == {"foo": "bar"}
    assert d["from_agent"] == "a"
    assert d["to_agent"] == "b"
    assert d["result"] == {"some": "res"}
    assert d["error"] == {"err": "msg"}

    msg_from = A2AMessage.from_dict(d)
    assert msg_from.type == MessageType.REQUEST
    assert msg_from.method == "test"
    assert msg_from.from_agent == "a"

    # Factories
    req = A2AMessage.request(
        "greet", {"name": "alice"}, from_agent="agent-a", to_agent="agent-b"
    )
    assert req.type == MessageType.REQUEST
    assert req.from_agent == "agent-a"

    resp = A2AMessage.response(req.id, {"text": "hello"}, from_agent="agent-b")
    assert resp.type == MessageType.RESPONSE
    assert resp.result == {"text": "hello"}

    err = A2AMessage.error_response("123", 500, "fail", data="extra")
    assert err.type == MessageType.ERROR
    assert err.error["code"] == 500
    assert err.error["data"] == "extra"


def test_wrappers_legacy():
    req_wrap = A2ARequest(method="ask", params={"q": "why?"})
    msg = req_wrap.to_message(from_agent="a", to_agent="b")
    assert msg.method == "ask"
    assert msg.from_agent == "a"

    # Response success
    resp_msg = A2AMessage.response(msg.id, {"ans": "because"})
    resp_wrap = A2AResponse.from_message(resp_msg, latency_ms=10.5)
    assert resp_wrap.success
    assert resp_wrap.result == {"ans": "because"}
    assert resp_wrap.latency_ms == 10.5

    # Response error
    err_msg = A2AMessage.error_response(msg.id, 404, "not found")
    resp_wrap_err = A2AResponse.from_message(err_msg)
    assert not resp_wrap_err.success
    assert resp_wrap_err.error_code == 404
    assert resp_wrap_err.error_message == "not found"
