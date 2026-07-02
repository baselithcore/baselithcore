"""
Plugin commands package.

Command handlers are imported lazily (PEP 562 ``__getattr__``) so that merely
registering the ``plugin`` subparser at CLI startup does not pull in heavy
dependency chains (e.g. ``manifest`` -> ``core.plugins`` -> ``fastapi``).
``register_parser`` stays eager because it is argparse-only and needed on every
invocation. Public attribute access (``plugin.create_plugin``) and
``from ...plugin import create_plugin`` keep working unchanged.
"""

import importlib
from typing import TYPE_CHECKING, Any

from .parser import register_parser

if TYPE_CHECKING:
    # Static-analysis-only imports: give type checkers/IDEs the real symbols
    # while keeping runtime resolution lazy via ``__getattr__`` below.
    from .config import config_get, config_reset, config_set, config_show
    from .create import create_plugin
    from .deps import deps_check, deps_install
    from .local import (
        delete_local_plugin,
        disable_local_plugin,
        enable_local_plugin,
        info_local_plugin,
        status_local_plugins,
        validate_local_plugin,
    )
    from .logs import plugin_logs
    from .manifest import export_manifest_cmd
    from .marketplace import (
        identity_cmd,
        info_plugin,
        install_plugin_cmd,
        login_cmd,
        logout_cmd,
        publish_plugin_cmd,
        search_plugins,
        uninstall_plugin_cmd,
        update_plugin_cmd,
    )
    from .sign import sign_plugin
    from .tree import plugin_tree

# Public name -> submodule that defines it. Resolved on first access.
_LAZY_EXPORTS: dict[str, str] = {
    "create_plugin": "create",
    "status_local_plugins": "local",
    "info_local_plugin": "local",
    "delete_local_plugin": "local",
    "enable_local_plugin": "local",
    "disable_local_plugin": "local",
    "validate_local_plugin": "local",
    "export_manifest_cmd": "manifest",
    "search_plugins": "marketplace",
    "info_plugin": "marketplace",
    "install_plugin_cmd": "marketplace",
    "uninstall_plugin_cmd": "marketplace",
    "update_plugin_cmd": "marketplace",
    "publish_plugin_cmd": "marketplace",
    "login_cmd": "marketplace",
    "logout_cmd": "marketplace",
    "identity_cmd": "marketplace",
    "deps_check": "deps",
    "deps_install": "deps",
    "config_show": "config",
    "config_set": "config",
    "config_get": "config",
    "config_reset": "config",
    "plugin_logs": "logs",
    "plugin_tree": "tree",
    "sign_plugin": "sign",
}

__all__ = [
    "config_get",
    "config_reset",
    "config_set",
    "config_show",
    "create_plugin",
    "delete_local_plugin",
    "deps_check",
    "deps_install",
    "disable_local_plugin",
    "enable_local_plugin",
    "export_manifest_cmd",
    "identity_cmd",
    "info_local_plugin",
    "info_plugin",
    "install_plugin_cmd",
    "login_cmd",
    "logout_cmd",
    "plugin_logs",
    "plugin_tree",
    "publish_plugin_cmd",
    "register_parser",
    "search_plugins",
    "sign_plugin",
    "status_local_plugins",
    "uninstall_plugin_cmd",
    "update_plugin_cmd",
    "validate_local_plugin",
]


def __getattr__(name: str) -> Any:
    """Lazily import and cache a command handler on first access (PEP 562)."""
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(f".{module_name}", __name__)
    attr = getattr(module, name)
    globals()[name] = attr  # cache so subsequent access skips __getattr__
    return attr


def __dir__() -> list[str]:
    return sorted(__all__)
