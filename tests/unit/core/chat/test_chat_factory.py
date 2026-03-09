import pytest
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
from core.chat.factory import (
    chat_service_factory,
    create_chat_service_from_config,
    load_chat_dependency_config,
    resolve_chat_service,
    _split_import_path,
    _invoke_service_factory,
)
from core.chat.dependencies import ChatDependencyConfig


@patch("core.chat.factory.create_default_dependencies")
@patch("core.chat.factory.ChatService")
def test_chat_service_factory(mock_service, mock_deps):
    service = chat_service_factory()
    mock_deps.assert_called_once()
    mock_service.assert_called_once()
    assert service == mock_service.return_value


@patch("core.chat.factory.create_default_dependencies")
@patch("core.chat.factory.ChatService")
def test_create_chat_service_from_config(mock_service, mock_deps):
    config = ChatDependencyConfig()
    service = create_chat_service_from_config(config)
    # It might be called with keyword config=config due to _invoke_dependencies_factory logic
    assert mock_deps.called
    assert service == mock_service.return_value


def test_load_chat_dependency_config_valid(tmp_path):
    config_file = tmp_path / "config.json"
    config_data = {"embedder_model": "test-model", "history_enabled": False}
    config_file.write_text(json.dumps(config_data))

    config = load_chat_dependency_config(config_file)
    assert config.embedder_model == "test-model"
    assert config.history_enabled is False


def test_load_chat_dependency_config_invalid(tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps([1, 2, 3]))  # Not a dict

    with pytest.raises(ValueError, match="must contain a JSON object"):
        load_chat_dependency_config(config_file)


@patch("core.chat.factory._load_config_from_path")
@patch("core.chat.factory._load_factory")
@patch("core.chat.factory._invoke_service_factory")
def test_resolve_chat_service(mock_invoke, mock_load_factory, mock_load_config):
    mock_load_factory.return_value = MagicMock()
    mock_load_config.return_value = MagicMock()

    resolve_chat_service(factory_path="factory", config_path=Path("config"))

    mock_load_config.assert_called_once_with(Path("config"))
    mock_load_factory.assert_called_once_with("factory")
    mock_invoke.assert_called_once()


def test_split_import_path():
    assert _split_import_path("module:attr") == ("module", "attr")
    assert _split_import_path("module.sub.attr") == ("module.sub", "attr")


def test_invoke_service_factory():
    mock_factory = MagicMock()
    config = MagicMock()

    # Signature 1: factory(config=config)
    _invoke_service_factory(mock_factory, config)
    mock_factory.assert_called_with(config=config)

    # Signature 2: factory(config) - positional
    mock_factory.reset_mock()
    mock_factory.side_effect = [TypeError("unexpected keyword"), MagicMock()]
    _invoke_service_factory(mock_factory, config)
    assert mock_factory.call_count == 2

    # Signature 3: factory() - no args
    mock_factory.reset_mock()
    mock_factory.side_effect = TypeError("missing arg")
    with pytest.raises(TypeError, match="must be callable without arguments"):
        _invoke_service_factory(mock_factory, None)


def test_invoke_dependencies_factory():
    from core.chat.factory import _invoke_dependencies_factory

    mock_factory = MagicMock()
    config = MagicMock()

    # Keyword success
    _invoke_dependencies_factory(mock_factory, config)
    mock_factory.assert_called_with(config=config)

    # Positional fallback
    mock_factory.reset_mock()
    mock_factory.side_effect = [TypeError("wrong kwarg"), MagicMock()]
    _invoke_dependencies_factory(mock_factory, config)
    assert mock_factory.call_count == 2

    # Positional fallback with None
    mock_factory.reset_mock()
    mock_factory.side_effect = [TypeError("wrong kwarg"), MagicMock()]
    _invoke_dependencies_factory(mock_factory, None)
    assert mock_factory.call_count == 2


@patch("core.chat.factory.get_chat_config")
def test_load_config_from_path_env(mock_get_config, tmp_path):
    from core.chat.factory import _load_config_from_path

    # Case 1: Path given
    path = tmp_path / "explicit.json"
    path.write_text("{}")
    assert _load_config_from_path(path) is not None

    # Case 2: Env given
    mock_get_config.return_value.service_config_file = str(path)
    assert _load_config_from_path(None) is not None

    # Case 3: Missing file
    with pytest.raises(FileNotFoundError):
        _load_config_from_path(Path("missing.json"))


@patch("core.chat.factory.import_module")
@patch("core.chat.factory.get_chat_config")
def test_load_factory_dynamic(mock_get_config, mock_import):
    from core.chat.factory import _load_factory

    # Default fallback
    mock_get_config.return_value.service_factory = None
    assert _load_factory(None) == create_chat_service_from_config

    # Custom factory
    mock_module = MagicMock()
    mock_factory = MagicMock()
    mock_import.return_value = mock_module
    setattr(mock_module, "my_factory", mock_factory)

    result = _load_factory("pkg.mod:my_factory")
    assert result == mock_factory

    # Not callable error
    setattr(mock_module, "not_callable", "string")
    with pytest.raises(TypeError, match="not callable"):
        _load_factory("pkg.mod:not_callable")
