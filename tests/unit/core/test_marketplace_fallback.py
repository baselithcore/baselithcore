import pytest
from core.api.factory import create_app


def test_app_startup_with_marketplace():
    """Verify the FastAPI app starts with the marketplace integration."""
    try:
        app = create_app()
        assert app is not None
    except Exception as e:
        pytest.fail(f"App failed to start: {e}")


def test_marketplace_cli_check():
    """Verify marketplace CLI check function returns a boolean."""
    from core.cli.commands.plugin.marketplace import _check_marketplace

    result = _check_marketplace()
    assert isinstance(result, bool)
