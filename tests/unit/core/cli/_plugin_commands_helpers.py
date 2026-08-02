"""Shared helpers for the CLI plugin-command test modules.

Not collected by pytest (leading underscore); imported by the
``test_plugin_commands_*`` siblings.
"""

from pathlib import Path

import yaml


def _make_plugin(
    tmp_path: Path, name: str, manifest: dict | None = None, disabled: bool = False
):
    """Create a minimal plugin directory for testing."""
    plugin_dir = tmp_path / "plugins" / name
    plugin_dir.mkdir(parents=True, exist_ok=True)

    plugin_py = "plugin.disabled" if disabled else "plugin.py"
    (plugin_dir / plugin_py).write_text(
        "from core.plugins.interface import Plugin\n"
        f"class {name.replace('-', '').title()}Plugin(Plugin):\n"
        "    async def initialize(self, config): pass\n"
    )

    if manifest:
        (plugin_dir / "manifest.yaml").write_text(
            yaml.dump(manifest, default_flow_style=False)
        )

    if not disabled:
        (plugin_dir / "__init__.py").write_text(f'"""{name} plugin."""\n')

    return plugin_dir


def _make_config(tmp_path: Path, config: dict):
    """Create a configs/plugins.yaml for testing."""
    config_dir = tmp_path / "configs"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "plugins.yaml"
    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False)
    return config_path
