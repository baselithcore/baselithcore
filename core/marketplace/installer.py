"""
Marketplace Plugin Installer.

Handles the physical installation of plugins from remote sources (Git),
validation of plugin structures, and dependency management.
"""

import asyncio
import logging
import shutil
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

from core.config.plugins import get_plugin_config
from core.marketplace.models import MarketplacePlugin

logger = logging.getLogger(__name__)


class InstallStatus(Enum):
    """Result status of a plugin installation."""

    SUCCESS = "success"
    FAILED = "failed"
    ALREADY_INSTALLED = "already_installed"
    VALIDATION_ERROR = "validation_error"


@dataclass
class InstallResult:
    """Details of a plugin installation attempt."""

    status: InstallStatus
    plugin_id: str
    destination: Optional[Path] = None
    error: Optional[str] = None


class PluginInstaller:
    """
    Handles the lifecycle of plugin installation from the marketplace.
    """

    def __init__(self):
        self.config = get_plugin_config()
        self.plugins_dir = Path(self.config.plugins_path)

    def _ensure_plugins_dir(self):
        """Ensure the plugins directory exists."""
        self.plugins_dir.mkdir(parents=True, exist_ok=True)

    async def install(
        self, plugin: MarketplacePlugin, branch: str = "main"
    ) -> InstallResult:
        """
        Install a plugin from its git repository.
        """
        if not plugin.git_url:
            return InstallResult(
                status=InstallStatus.FAILED,
                plugin_id=plugin.id,
                error="Plugin does not have a git URL",
            )

        plugin_dest = self.plugins_dir / plugin.name

        if plugin_dest.exists():
            return InstallResult(
                status=InstallStatus.ALREADY_INSTALLED,
                plugin_id=plugin.id,
                destination=plugin_dest,
            )

        self._ensure_plugins_dir()

        logger.info(f"Installing plugin {plugin.name} from {plugin.git_url}")

        try:
            # Clone repository
            process = await asyncio.create_subprocess_exec(
                "git",
                "clone",
                "--depth",
                "1",
                "-b",
                branch,
                plugin.git_url,
                str(plugin_dest),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                error_msg = stderr.decode().strip()
                logger.error(f"Git clone failed for {plugin.name}: {error_msg}")
                return InstallResult(
                    status=InstallStatus.FAILED,
                    plugin_id=plugin.id,
                    error=f"Git clone failed: {error_msg}",
                )

            # Cleanup .git directory to keep it as a simple directory
            shutil.rmtree(plugin_dest / ".git", ignore_errors=True)

            # Install dependencies if pyproject.toml exists
            if (plugin_dest / "pyproject.toml").exists():
                logger.info(f"Installing dependencies for {plugin.name}")
                # We use 'pip install -e' or just 'pip install' depending on environment
                # For core, we usually want them installed in the current environment
                dep_process = await asyncio.create_subprocess_exec(
                    "pip",
                    "install",
                    str(plugin_dest),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await dep_process.communicate()

            return InstallResult(
                status=InstallStatus.SUCCESS,
                plugin_id=plugin.id,
                destination=plugin_dest,
            )

        except Exception as e:
            logger.exception(f"Unexpected error installing plugin {plugin.name}")
            # Cleanup on failure
            if plugin_dest.exists():
                shutil.rmtree(plugin_dest, ignore_errors=True)
            return InstallResult(
                status=InstallStatus.FAILED, plugin_id=plugin.id, error=str(e)
            )

    async def uninstall(self, plugin_name: str) -> bool:
        """
        Remove a plugin directory.
        """
        plugin_dir = self.plugins_dir / plugin_name
        if plugin_dir.exists() and plugin_dir.is_dir():
            try:
                # Try to uninstall from pip first if it was installed as a package
                subprocess.run(
                    ["pip", "uninstall", "-y", plugin_name], capture_output=True
                )
                shutil.rmtree(plugin_dir)
                return True
            except Exception as e:
                logger.error(f"Failed to uninstall plugin {plugin_name}: {e}")
                return False
        return False
