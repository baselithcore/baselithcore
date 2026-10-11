"""
Local plugin management commands (Facade).
"""

from .local_manage import (
    delete_local_plugin,
    disable_local_plugin,
    enable_local_plugin,
)
from .local_shared import PLUGINS_CONFIG_PATH, console
from .local_status import (
    info_local_plugin,
    status_local_plugins,
)
from .local_validate import (
    validate_local_plugin,
)

__all__ = [
    "PLUGINS_CONFIG_PATH",
    "console",
    "delete_local_plugin",
    "disable_local_plugin",
    "enable_local_plugin",
    "info_local_plugin",
    "status_local_plugins",
    "validate_local_plugin",
]
