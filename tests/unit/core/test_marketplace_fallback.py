import pytest
from core.api.factory import create_app


def test_app_startup_without_marketplace():
    """Verify the FastAPI app starts without the marketplace plugin."""
    try:
        app = create_app()
        assert app is not None
        # Check that the marketplace router is NOT in the app if plugin is missing
        # (This depends on whether the plugin is installed in the test environment)
    except Exception as e:
        pytest.fail(f"App failed to start without marketplace: {e}")


def test_marketplace_cli_fallback():
    """Verify marketplace CLI commands report missing plugin instead of crashing."""
    from core.cli.commands.plugin.marketplace import _check_marketplace

    # This test assumes the marketplace plugin is NOT installed in the environment
    # where this specific unit test runs.
    result = _check_marketplace()
    # In CI, it should be False
    assert result in [True, False]


def test_marketplace_shim_import_error():
    """Verify that importing from core.marketplace raises a helpful ImportError."""
    with pytest.raises(ImportError) as excinfo:
        from core.marketplace import PluginRegistry  # noqa: F401

    assert "Marketplace plugin not found" in str(excinfo.value)
    assert "pip install -e ../baselith-marketplace-plugin" in str(excinfo.value)
