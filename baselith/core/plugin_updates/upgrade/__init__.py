"""Version-specific upgrade instructions (notification only, never executed).

The framework follows the notify-and-instruct model: when a newer core release
exists, the administrator is told what changed and how to upgrade this
deployment with the standard tools of its installation method. Nothing here
runs a command, downloads a release or changes the deployment.
"""

from .build import (
    Deployment,
    build_upgrade_instructions,
    plugin_install_guidance,
    read_namespace,
)
from .compat import installed_bounds, plugin_compatibility
from .custom import MAX_INSTRUCTIONS_BYTES, render_instructions
from .method import detect_install_method, is_source_checkout
from .path import upgrade_path
from .templates import (
    TemplateContext,
    backup_step,
    default_steps,
    distribution_step,
    post_checks,
)

__all__ = [
    "MAX_INSTRUCTIONS_BYTES",
    "Deployment",
    "TemplateContext",
    "backup_step",
    "build_upgrade_instructions",
    "default_steps",
    "detect_install_method",
    "distribution_step",
    "installed_bounds",
    "is_source_checkout",
    "plugin_compatibility",
    "plugin_install_guidance",
    "post_checks",
    "read_namespace",
    "render_instructions",
    "upgrade_path",
]
