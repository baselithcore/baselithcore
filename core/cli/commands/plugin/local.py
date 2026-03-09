"""
Local plugin management commands (Facade).
"""

from .local_shared import PLUGINS_CONFIG_PATH, console
from .local_status import (
    status_local_plugins,
    info_local_plugin,
)
from .local_manage import (
    delete_local_plugin,
    disable_local_plugin,
    enable_local_plugin,
)
from .local_validate import (
    validate_local_plugin,
)

__all__ = [
    "status_local_plugins",
    "info_local_plugin",
    "delete_local_plugin",
    "disable_local_plugin",
    "enable_local_plugin",
    "validate_local_plugin",
    "PLUGINS_CONFIG_PATH",
    "console",
]
