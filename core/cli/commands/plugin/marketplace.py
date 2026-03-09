"""
Marketplace plugin commands.
"""

import asyncio
from typing import Optional
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from core.cli.ui import console, print_error, print_success, print_step, print_warning


def _check_marketplace():
    """Check if marketplace plugin is installed and provide instructions if not."""
    try:
        import plugins.marketplace  # noqa: F401

        return True
    except (ImportError, ModuleNotFoundError):
        print_error("Marketplace plugin not found.")
        console.print(
            "\nTo use marketplace features, please install the marketplace plugin project:"
        )
        console.print(
            "  [bold cyan]pip install -e ../baselith-marketplace-plugin[/bold cyan]\n"
        )
        return False


def search_plugins(query: str) -> int:
    """
    Search for plugins in the marketplace.
    """
    if not _check_marketplace():
        return 1

    from plugins.marketplace.registry import plugin_registry

    with console.status(f"[bold green]Searching marketplace for '{query}'..."):
        try:
            results = asyncio.run(plugin_registry.search(query))
        except Exception as e:
            print_error(f"Failed to search marketplace: {e}")
            return 1

    if not results:
        console.print(f"[yellow]No plugins found matching '{query}'.[/yellow]")
        return 0

    table = Table(
        title=f"Search Results for '{query}'",
        title_style="bold blue",
        show_header=True,
        header_style="bold magenta",
        expand=True,
    )
    table.add_column("Plugin ID", style="cyan", no_wrap=True)
    table.add_column("Name", style="bold")
    table.add_column("Version", justify="right")
    table.add_column("Description", style="dim")

    for meta in results:
        table.add_row(
            meta.id,
            meta.name,
            f"v{meta.version}",
            meta.description[:60] + "..."
            if len(meta.description) > 60
            else meta.description,
        )

    console.print(table)
    return 0


def info_plugin(plugin_id: str) -> int:
    """
    Get detailed information about a marketplace plugin.
    """
    if not _check_marketplace():
        return 1

    from plugins.marketplace.registry import plugin_registry

    with console.status(f"[bold green]Fetching details for '{plugin_id}'..."):
        try:
            metadata = asyncio.run(plugin_registry.get(plugin_id))
        except Exception as e:
            print_error(f"Failed to fetch plugin info: {e}")
            return 1

    if not metadata:
        print_error(f"Plugin '{plugin_id}' not found in the marketplace.")
        return 1

    details = Text()
    details.append("Name: ", style="bold cyan")
    details.append(f"{metadata.name}\n")
    details.append("ID: ", style="bold cyan")
    details.append(f"{metadata.id}\n")
    details.append("Version: ", style="bold cyan")
    details.append(f"{metadata.version}\n")
    details.append("Author: ", style="bold cyan")
    details.append(f"{metadata.author}\n")
    details.append("Tags: ", style="bold cyan")
    details.append(f"{', '.join(metadata.tags) if metadata.tags else 'None'}\n\n")

    details.append("Description:\n", style="bold cyan")
    details.append(f"{metadata.description}")

    panel = Panel(
        details,
        title=f"[bold]Plugin: {metadata.name}[/bold]",
        border_style="blue",
        padding=(1, 2),
    )
    console.print()
    console.print(panel)
    console.print()
    return 0


def install_plugin_cmd(
    plugin_id: str, version: Optional[str] = None, force: bool = False
) -> int:
    """
    Install a plugin from the marketplace.
    """
    if not _check_marketplace():
        return 1

    from plugins.marketplace.installer import PluginInstaller, InstallStatus

    print_step(f"Preparing to install '{plugin_id}'...")
    installer = PluginInstaller()

    with console.status(f"[bold green]Installing plugin '{plugin_id}'..."):
        try:
            result = asyncio.run(installer.install(plugin_id, version, force))
        except Exception as e:
            print_error(f"Installation failed with error: {e}")
            return 1

    if result.status == InstallStatus.SUCCESS:
        print_success(f"Successfully installed plugin '{plugin_id}' v{result.version}")
        if result.installed_path:
            console.print(f"  [dim]Installed at: {result.installed_path}[/dim]")
        return 0
    elif result.status == InstallStatus.ALREADY_INSTALLED:
        print_warning(f"Plugin '{plugin_id}' is already installed (v{result.version}).")
        console.print(
            "  [dim]Use --force to reinstall or the 'update' command to upgrade.[/dim]"
        )
        return 0
    else:
        print_error(f"Failed to install '{plugin_id}': {result.message}")
        return 1


def uninstall_plugin_cmd(plugin_id: str) -> int:
    """
    Uninstall a plugin.
    """
    if not _check_marketplace():
        return 1

    from plugins.marketplace.installer import PluginInstaller, InstallStatus

    print_step(f"Preparing to uninstall '{plugin_id}'...")
    installer = PluginInstaller()

    with console.status(f"[bold red]Uninstalling plugin '{plugin_id}'..."):
        try:
            result = asyncio.run(installer.uninstall(plugin_id))
        except Exception as e:
            print_error(f"Uninstallation failed with error: {e}")
            return 1

    if result.status == InstallStatus.SUCCESS:
        print_success(f"Successfully uninstalled plugin '{plugin_id}'.")
        return 0
    else:
        print_error(f"Failed to uninstall '{plugin_id}': {result.message}")
        return 1


def update_plugin_cmd(plugin_id: str) -> int:
    """
    Update an installed plugin to its latest version.
    """
    if not _check_marketplace():
        return 1

    from plugins.marketplace.installer import PluginInstaller, InstallStatus

    print_step(f"Checking for updates for '{plugin_id}'...")
    installer = PluginInstaller()

    with console.status(f"[bold green]Updating plugin '{plugin_id}'..."):
        try:
            result = asyncio.run(installer.update(plugin_id))
        except Exception as e:
            print_error(f"Update failed with error: {e}")
            return 1

    if result.status == InstallStatus.SUCCESS:
        print_success(f"Successfully updated plugin '{plugin_id}' to v{result.version}")
        return 0
    elif result.status == InstallStatus.ALREADY_INSTALLED:
        print_success(
            f"Plugin '{plugin_id}' is already at the latest version (v{result.version})."
        )
        return 0
    else:
        print_error(f"Failed to update '{plugin_id}': {result.message}")
        return 1


def publish_plugin_cmd(path: str, admin_key: Optional[str] = None) -> int:
    """
    Publish a local plugin to the marketplace hub.
    """
    if not _check_marketplace():
        return 1

    from plugins.marketplace.publisher import PluginPublisher

    print_step(f"Preparing to publish plugin from '{path}'...")
    publisher = PluginPublisher()

    with console.status("[bold green]Validating and uploading plugin..."):
        try:
            result = asyncio.run(publisher.publish(path, admin_key))
        except Exception as e:
            print_error(f"Publication failed with error: {e}")
            return 1

    if result.get("status") == "success":
        print_success(result.get("message", "Plugin successfully submitted!"))
        console.print(f"  [dim]Plugin ID: {result.get('plugin_id')}[/dim]")
        console.print(f"  [dim]Version: {result.get('version')}[/dim]")
        console.print(
            "\n[bold yellow]Note:[/bold yellow] Your plugin is now pending review by the hub administrator."
        )
        return 0
    else:
        print_error(f"Failed to publish plugin: {result.get('message')}")
        if "issues" in result:
            for issue in result["issues"]:
                console.print(f"  [red]- {issue}[/red]")
        return 1
