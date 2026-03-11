"""
Unit tests for the centralized Marketplace logic.
"""

import json
import pytest
from unittest.mock import patch, MagicMock

from core.marketplace import (
    PluginRegistry,
    PluginInstaller,
    PluginValidator,
    PluginCategory,
)
from core.marketplace.models import RegistryData, MarketplacePlugin


@pytest.fixture
def mock_registry_data():
    return {
        "version": "1.0",
        "last_updated": "2024-03-11",
        "plugins": [
            {
                "id": "test.plugin",
                "name": "test-plugin",
                "author": "Baselith",
                "version": "1.0.0",
                "description": "A test plugin",
                "category": "utility",
                "status": "stable",
                "git_url": "https://github.com/baselith/test-plugin",
            },
            {
                "id": "security.scanner",
                "name": "security-scanner",
                "author": "Baselith",
                "version": "0.1.0",
                "description": "Scans for vulnerabilities",
                "category": "security",
                "status": "beta",
                "git_url": "https://github.com/baselith/security-scanner",
            },
        ],
        "categories": [],
    }


@pytest.mark.asyncio
async def test_registry_fetch_mock(mock_registry_data, tmp_path):
    registry = PluginRegistry(cache_dir=tmp_path)

    with patch("httpx.AsyncClient.get") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = json.dumps(mock_registry_data)
        mock_get.return_value = mock_response

        data = await registry.fetch(force=True)

        assert data.version == "1.0"
        assert len(data.plugins) == 2
        assert data.plugins[0].name == "test-plugin"


@pytest.mark.asyncio
async def test_registry_search(mock_registry_data, tmp_path):
    registry = PluginRegistry(cache_dir=tmp_path)
    registry._data = RegistryData.model_validate(mock_registry_data)

    # Text search
    results = await registry.search(query="security")
    assert len(results) == 1
    assert results[0].id == "security.scanner"

    # Category search
    results = await registry.search(category=PluginCategory.UTILITY)
    assert len(results) == 1
    assert results[0].id == "test.plugin"


def test_validator(tmp_path):
    validator = PluginValidator()

    # Test invalid path
    result = validator.validate(tmp_path / "nonexistent")
    assert not result.is_valid

    # Test valid simulation
    plugin_dir = tmp_path / "my_plugin"
    plugin_dir.mkdir()
    (plugin_dir / "__init__.py").touch()
    (plugin_dir / "manifest.yaml").write_text("name: my_plugin\nversion: 1.0.0")

    result = validator.validate(plugin_dir)
    assert result.is_valid
    assert len(result.errors) == 0


@pytest.mark.asyncio
async def test_installer_already_installed(tmp_path):
    installer = PluginInstaller()
    installer.plugins_dir = tmp_path

    plugin_dir = tmp_path / "test-plugin"
    plugin_dir.mkdir()

    plugin = MarketplacePlugin(
        id="test.plugin",
        name="test-plugin",
        author="Baselith",
        version="1.0.0",
        git_url="http://example.com",
    )

    result = await installer.install(plugin)
    assert result.status.value == "already_installed"
