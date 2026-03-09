"""
Shared utilities and constants for local plugin commands.
"""

from pathlib import Path
from core.cli.ui import console

PLUGINS_CONFIG_PATH = Path("configs") / "plugins.yaml"

__all__ = ["PLUGINS_CONFIG_PATH", "console"]
