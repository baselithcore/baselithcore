import sys
from importlib.machinery import ModuleSpec
from unittest.mock import MagicMock


# Refined mocking to avoid 'ValueError: torch.__spec__ is not set' and 'AttributeError: Tensor' during collection
def _mock_module(name):
    m = MagicMock()
    m.__name__ = name
    m.__spec__ = ModuleSpec(name, None)
    m.__version__ = "2.3.0"
    sys.modules[name] = m
    return m


_mock_module("sentence_transformers")
_mock_module("torch")
_mock_module("torch.utils")
_mock_module("torch.utils.data")

from unittest.mock import AsyncMock, patch  # noqa: E402

import pytest  # noqa: E402

from core.services.indexing.service import IndexingService  # noqa: E402


@pytest.fixture
def mock_vectorstore():
    mock = AsyncMock()
    mock.index = AsyncMock(return_value=None)
    mock.delete_document = AsyncMock(return_value=None)
    return mock


@pytest.fixture
def mock_embedder():
    mock = MagicMock()
    return mock


@pytest.fixture
def mock_config():
    mock = MagicMock()
    mock.embedding_model = "test-model"
    mock.collection_name = "test-collection"
    # Real ints: these feed range/semaphore math (MagicMock breaks comparisons).
    mock.index_batch_size = 32
    mock.index_max_concurrency = 8
    return mock


@pytest.fixture
def indexing_service(mock_vectorstore, mock_embedder, mock_config):
    with (
        patch(
            "core.services.indexing.service.get_vectorstore_config",
            return_value=mock_config,
        ),
        patch(
            "core.services.indexing.service.get_processing_config",
            return_value=MagicMock(),
        ),
        patch(
            "core.services.indexing.service.get_vectorstore_service",
            return_value=mock_vectorstore,
        ),
        patch(
            "core.services.indexing.service.get_embedder", return_value=mock_embedder
        ),
    ):
        service = IndexingService(
            vectorstore_service=mock_vectorstore,
            embedder=mock_embedder,
            config=mock_config,
        )
        return service
