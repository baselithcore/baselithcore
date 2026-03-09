import sys
from unittest.mock import MagicMock, AsyncMock, patch

# Mock heavy dependencies before they are imported by core.services.chat.service
sys.modules["sentence_transformers"] = MagicMock()
sys.modules["torch"] = MagicMock()

import pytest  # noqa: E402
from core.services.chat.service import (  # noqa: E402
    ChatService,
    ChatServiceConfig,
    _next_stream_chunk,
    _STREAM_EOF,
)
from core.models.chat import ChatRequest  # noqa: E402
from core.services.chat.exceptions import ChatServiceError  # noqa: E402


@pytest.fixture
def chat_config():
    return ChatServiceConfig(streaming_enabled=True, history_enabled=True)


@pytest.fixture
def mock_plugin_registry():
    return MagicMock()


@pytest.fixture
def chat_service(chat_config, mock_plugin_registry):
    return ChatService(config=chat_config, plugin_registry=mock_plugin_registry)


def test_initialization(chat_config, mock_plugin_registry):
    service = ChatService(config=chat_config, plugin_registry=mock_plugin_registry)
    assert service.config == chat_config
    assert service.plugin_registry == mock_plugin_registry
    assert service._embedder is None
    assert service._reranker is None


def test_next_stream_chunk():
    it = iter(["a", "b"])
    assert _next_stream_chunk(it) == "a"
    assert _next_stream_chunk(it) == "b"
    assert _next_stream_chunk(it) is _STREAM_EOF


def test_embedder_property(chat_service):
    mock_embedder = MagicMock()
    with patch("core.nlp.get_embedder", return_value=mock_embedder) as mock_get:
        assert chat_service.embedder == mock_embedder
        assert chat_service.embedder == mock_embedder  # Second call uses cache
        mock_get.assert_called_once_with(chat_service.config.embedder_model)


def test_embedder_import_error(chat_service):
    with patch("core.nlp.get_embedder", side_effect=ImportError("NLP missing")):
        with pytest.raises(ChatServiceError, match="NLP module not reachable"):
            _ = chat_service.embedder


def test_reranker_property(chat_service):
    mock_reranker = MagicMock()
    with patch("core.nlp.get_reranker", return_value=mock_reranker) as mock_get:
        assert chat_service.reranker == mock_reranker
        assert chat_service.reranker == mock_reranker
        mock_get.assert_called_once_with(chat_service.config.reranker_model)


def test_reranker_import_error(chat_service):
    with patch("core.nlp.get_reranker", side_effect=ImportError("Reranker missing")):
        with pytest.raises(ChatServiceError, match="Re-ranking module not reachable"):
            _ = chat_service.reranker


def test_history_manager_property(chat_service):
    manager = chat_service.history_manager
    assert manager is not None
    assert chat_service.history_manager == manager


def test_agent_property_no_registry():
    service = ChatService(plugin_registry=None)
    with pytest.raises(
        ChatServiceError, match="Orchestrator requires active plugin_registry"
    ):
        _ = service.agent


def test_agent_property_success(chat_service):
    mock_orch_cls = MagicMock()
    with patch("core.orchestration.Orchestrator", mock_orch_cls):
        agent = chat_service.agent
        mock_orch_cls.assert_called_once_with(
            plugin_registry=chat_service.plugin_registry
        )
        assert chat_service.agent == agent


def test_agent_import_error(chat_service):
    with patch(
        "core.orchestration.Orchestrator", side_effect=ImportError("Orch missing")
    ):
        with pytest.raises(ChatServiceError, match="Orchestration layer missing"):
            _ = chat_service.agent


def test_handle_chat_sync(chat_service):
    req = ChatRequest(query="hello", conversation_id="123")
    mock_agent = MagicMock()
    mock_agent.process = AsyncMock(
        return_value={
            "response": "hi",
            "metadata": {"foo": "bar"},
            "sources": [{"title": "source1"}],
        }
    )

    with (
        patch.object(ChatService, "agent", new=mock_agent),
        patch.object(chat_service, "_record_metric") as mock_metric,
    ):
        response = chat_service.handle_chat(req)
        assert response.answer == "hi"
        assert response.metadata == {"foo": "bar"}
        assert response.sources == [{"title": "source1"}]
        mock_metric.assert_any_call("chat_requests_total", route="sync")


def test_handle_chat_sync_in_async_loop(chat_service):
    req = ChatRequest(query="hello")
    mock_loop = MagicMock()
    mock_loop.is_running.return_value = True
    with patch("asyncio.get_running_loop", return_value=mock_loop):
        with pytest.raises(RuntimeError, match="Attempted synchronous chat execution"):
            chat_service.handle_chat(req)


@pytest.mark.asyncio
async def test_handle_chat_async(chat_service):
    req = ChatRequest(query="hello")
    mock_agent = MagicMock()
    mock_agent.process = AsyncMock(return_value={"response": "async hi"})

    with patch.object(ChatService, "agent", new=mock_agent):
        response = await chat_service.handle_chat_async(req)
        assert response.answer == "async hi"


@pytest.mark.asyncio
async def test_handle_chat_async_error(chat_service):
    req = ChatRequest(query="error")
    mock_agent = MagicMock()
    mock_agent.process = AsyncMock(side_effect=Exception("Async fail"))
    with patch.object(ChatService, "agent", new=mock_agent):
        with pytest.raises(Exception, match="Async fail"):
            await chat_service.handle_chat_async(req)


def test_handle_chat_stream(chat_service):
    req = ChatRequest(query="stream")
    mock_agent = MagicMock()
    mock_agent.process_stream.return_value = iter(["token1", "token2"])

    with patch.object(ChatService, "agent", new=mock_agent):
        stream = chat_service.handle_chat_stream(req)
        assert list(stream) == ["token1", "token2"]


def test_record_metric_import_error(chat_service):
    """Tests that _record_metric handles ImportError correctly."""
    with patch(
        "builtins.__import__",
        side_effect=lambda name, *args, **kwargs: (
            exec('raise ImportError("No metrics")')
            if "metrics" in name
            else __import__(name, *args, **kwargs)
        ),
    ):
        chat_service._record_metric("chat_requests_total", route="test")
        # Should NOT raise, and should just pass.
        pass


def test_handle_chat_stream_disabled(chat_service):
    chat_service.config.streaming_enabled = False
    req = ChatRequest(query="sync")
    with patch.object(chat_service, "handle_chat") as mock_handle:
        mock_handle.return_value = MagicMock(answer="sync answer")
        stream = chat_service.handle_chat_stream(req)
        assert list(stream) == ["sync answer"]


def test_handle_chat_stream_error(chat_service):
    req = ChatRequest(query="error")
    mock_agent = MagicMock()
    mock_agent.process_stream.side_effect = Exception("Stream fail")
    with patch.object(ChatService, "agent", new=mock_agent):
        stream = chat_service.handle_chat_stream(req)
        assert "❌ Critical internal error" in next(stream)


@pytest.mark.asyncio
async def test_handle_chat_stream_async(chat_service):
    req = ChatRequest(query="async stream")
    with patch.object(
        chat_service, "handle_chat_stream", return_value=iter(["chunk1", "chunk2"])
    ):
        stream = await chat_service.handle_chat_stream_async(req)
        chunks = []
        async for chunk in stream:
            chunks.append(chunk)
        assert chunks == ["chunk1", "chunk2"]


def test_record_metric_success(chat_service):
    mock_total = MagicMock()
    mock_err = MagicMock()
    mock_lat = MagicMock()
    with (
        patch("core.observability.metrics.CHAT_REQUESTS_TOTAL", mock_total),
        patch("core.observability.metrics.CHAT_REQUEST_ERRORS_TOTAL", mock_err),
        patch("core.observability.metrics.CHAT_REQUEST_LATENCY_SECONDS", mock_lat),
    ):
        chat_service._record_metric("chat_requests_total", route="sync")
        mock_total.labels.assert_called_with(route="sync")

        chat_service._record_metric("chat_request_errors_total", route="async")
        mock_err.labels.assert_called_with(route="async", reason="exception")

        chat_service._record_metric("chat_request_latency", route="stream", value=0.5)
        mock_lat.labels.assert_called_with(route="stream")


def test_record_metric_import_error_direct(chat_service):
    with patch(
        "core.observability.metrics.CHAT_REQUESTS_TOTAL", side_effect=ImportError
    ):
        chat_service._record_metric("chat_requests_total", route="sync")
