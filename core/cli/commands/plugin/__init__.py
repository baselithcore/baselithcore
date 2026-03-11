"""
Plugin commands package.
"""

from .create import create_plugin
from .local import (
    status_local_plugins,
    info_local_plugin,
    delete_local_plugin,
    enable_local_plugin,
    disable_local_plugin,
    validate_local_plugin,
)
from .manifest import export_manifest_cmd
from .marketplace import (
    search_plugins,
    info_plugin,
    install_plugin_cmd,
    uninstall_plugin_cmd,
    update_plugin_cmd,
    publish_plugin_cmd,
    login_cmd,
    logout_cmd,
    identity_cmd,
)
from .deps import deps_check, deps_install
from .config import config_show, config_set, config_get, config_reset
from .logs import plugin_logs
from .tree import plugin_tree
from .parser import register_parser

__all__ = [
    "create_plugin",
    "status_local_plugins",
    "info_local_plugin",
    "delete_local_plugin",
    "enable_local_plugin",
    "disable_local_plugin",
    "validate_local_plugin",
    "export_manifest_cmd",
    "search_plugins",
    "info_plugin",
    "install_plugin_cmd",
    "uninstall_plugin_cmd",
    "update_plugin_cmd",
    "publish_plugin_cmd",
    "login_cmd",
    "logout_cmd",
    "identity_cmd",
    "deps_check",
    "deps_install",
    "config_show",
    "config_set",
    "config_get",
    "config_reset",
    "plugin_logs",
    "plugin_tree",
    "register_parser",
]
