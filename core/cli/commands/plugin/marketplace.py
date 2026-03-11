"""
Marketplace Management CLI Commands.

Provides commands to discover, search, and install plugins from the
Baselith Marketplace.
"""

import asyncio
from typing import Optional

from rich.console import Console
from rich.table import Table

from core.marketplace import PluginRegistry, PluginInstaller, PluginCategory
from core.marketplace.auth import CredentialsManager, AuthService
from core.marketplace.publisher import PluginPublisher

console = Console()


def search_plugins(
    query: Optional[str] = None, category: str = "all", force_refresh: bool = False
):
    """
    Search for plugins in the Baselith Marketplace.
    """

    async def _run():
        registry = PluginRegistry()

        try:
            cat = PluginCategory(category.lower())
        except ValueError:
            console.print(f"[red]Error: Invalid category '{category}'.[/red]")
            return

        console.print("[cyan]Searching marketplace...[/cyan]")

        try:
            plugins = await registry.search(query=query, category=cat)

            if not plugins:
                console.print(
                    "[yellow]No plugins found matching your criteria.[/yellow]"
                )
                return

            table = Table(title="Baselith Marketplace")
            table.add_column("Plugin ID", style="cyan")
            table.add_column("Name", style="bold green")
            table.add_column("Status", style="magenta")
            table.add_column("Description")
            table.add_column("Stars", justify="right")

            for p in plugins:
                table.add_row(
                    p.id, p.name, p.status.value, p.description or "", str(p.stars)
                )

            console.print(table)
            console.print(
                f"\n[dim]Found {len(plugins)} plugins. Use 'baselith plugin marketplace info <id>' for details.[/dim]"
            )

        except Exception as e:
            console.print(f"[red]Error searching marketplace: {e}[/red]")

    asyncio.run(_run())


def info_plugin(plugin_id: str):
    """
    Show detailed information about a marketplace plugin.
    """

    async def _run():
        registry = PluginRegistry()
        plugin = await registry.get_plugin(plugin_id)

        if not plugin:
            console.print(
                f"[red]Error: Plugin '{plugin_id}' not found in marketplace.[/red]"
            )
            return

        console.print(f"[bold green]Plugin: {plugin.name}[/bold green] ({plugin.id})")
        console.print(f"Status: [magenta]{plugin.status.value}[/magenta]")
        console.print(f"Author: [cyan]{plugin.author}[/cyan]")
        console.print(f"Description: {plugin.description}")
        if plugin.git_url:
            console.print(f"Repository: [blue]{plugin.git_url}[/blue]")
        if plugin.tags:
            console.print(f"Tags: [yellow]{', '.join(plugin.tags)}[/yellow]")
        console.print(f"Stars: {plugin.stars} | Downloads: {plugin.downloads}")

    asyncio.run(_run())


def install_plugin_cmd(
    plugin_id: str, version: Optional[str] = None, force: bool = False
):
    """
    Install a plugin from the marketplace.
    """

    async def _run():
        registry = PluginRegistry()
        installer = PluginInstaller()

        plugin = await registry.get_plugin(plugin_id)
        if not plugin:
            console.print(
                f"[red]Error: Plugin '{plugin_id}' not found in marketplace.[/red]"
            )
            return

        console.print(f"[cyan]Installing {plugin.name}...[/cyan]")

        # Use version if provided, otherwise 'main'
        branch = version or "main"
        result = await installer.install(plugin, branch=branch)

        if result.status.value == "success":
            console.print(
                f"[bold green]Successfully installed {plugin.name} to {result.destination}[/bold green]"
            )
            console.print("[dim]Restart Baselith to load the new plugin.[/dim]")
        elif result.status.value == "already_installed":
            console.print(
                f"[yellow]Plugin {plugin.name} is already installed at {result.destination}.[/yellow]"
            )
        else:
            console.print(f"[red]Failed to install {plugin.name}: {result.error}[/red]")

    asyncio.run(_run())


def uninstall_plugin_cmd(plugin_id: str):
    """
    Uninstall a plugin.
    """

    async def _run():
        installer = PluginInstaller()

        if await installer.uninstall(plugin_id):
            console.print(
                f"[bold green]Successfully uninstalled {plugin_id}.[/bold green]"
            )
        else:
            console.print(
                f"[red]Error: Could not uninstall plugin '{plugin_id}'. Ensure the name is correct.[/red]"
            )

    asyncio.run(_run())


def update_plugin_cmd(plugin_id: str):
    """
    Update an existing plugin from the marketplace.
    """

    async def _run():
        # Simply uninstall and reinstall for now
        installer = PluginInstaller()
        registry = PluginRegistry()

        plugin = await registry.get_plugin(plugin_id)
        if not plugin:
            console.print(
                f"[red]Error: Plugin '{plugin_id}' not found in marketplace.[/red]"
            )
            return

        console.print(f"[cyan]Updating {plugin.name}...[/cyan]")
        await installer.uninstall(plugin.name)
        result = await installer.install(plugin)

        if result.status.value == "success":
            console.print(
                f"[bold green]Successfully updated {plugin.name}.[/bold green]"
            )
        else:
            console.print(f"[red]Failed to update {plugin.name}: {result.error}[/red]")

    asyncio.run(_run())


def login_cmd():
    """
    Authenticate with the marketplace (Supports API Keys and future JWT login).
    """
    console.print("[cyan]Welcome to Baselith Marketplace Authentication.[/cyan]")
    console.print(
        "[dim]Note: A future update will introduce interactive centralized browser login.[/dim]\n"
    )

    auth_input = console.input("Please enter your Marketplace API Key or JWT Token: ")
    if not auth_input.strip():
        console.print("[red]Error: Credentials cannot be empty.[/red]")
        return

    manager = CredentialsManager()

    # Simple check for JWT structure (header.payload.signature)
    if len(auth_input.split(".")) == 3:
        token = auth_input.strip()
        manager.save_token(token)
        console.print(
            "[bold green]Successfully saved Authentication Token.[/bold green]"
        )

        # Attempt to sync profile immediately
        async def _sync():
            auth_service = AuthService()
            if await auth_service.sync_user_profile():
                console.print("[cyan]Verified identity and synced user profile.[/cyan]")

        asyncio.run(_sync())
    else:
        manager.save_api_key(auth_input.strip())
        console.print("[bold green]Successfully saved API Key.[/bold green]")


def logout_cmd():
    """
    Remove cached marketplace credentials.
    """
    manager = CredentialsManager()
    manager.delete_credentials()
    console.print(
        "[bold green]Successfully logged out. Cached credentials removed.[/bold green]"
    )


def identity_cmd():
    """
    Show the currently logged-in marketplace identity.
    """

    async def _run():
        auth_service = AuthService()
        manager = CredentialsManager()

        token = manager.load_token()
        if token:
            console.print("[cyan]Verifying marketplace session...[/cyan]")
            result = await auth_service.get_current_identity()

            if result["status"] == "success":
                user = result["user"]
                email = user.get("email") or user.get("username") or "Unknown"
                console.print(f"[bold green]Authenticated as:[/bold green] {email}")

                # Display additional info if available
                if "roles" in user:
                    roles = user["roles"]
                    if isinstance(roles, list):
                        console.print(f"Roles: [magenta]{', '.join(roles)}[/magenta]")

                if "tenant_id" in user:
                    console.print(f"Tenant: [cyan]{user['tenant_id']}[/cyan]")
            else:
                console.print(
                    f"[yellow]Token found but verification failed: {result.get('message')}[/yellow]"
                )
                console.print("[dim]You may need to login again.[/dim]")
        else:
            api_key = manager.load_api_key()
            if api_key:
                console.print(
                    "[bold green]Authenticated via API Key (Legacy).[/bold green]"
                )
                console.print(f"Key Prefix: [dim]{api_key[:8]}...[/dim]")
            else:
                console.print("[yellow]Not authenticated.[/yellow]")
                console.print(
                    "Use 'baselith plugin marketplace login' to authenticate."
                )

    asyncio.run(_run())


def publish_plugin_cmd(path: str, key: Optional[str] = None):
    """
    Publish a plugin to the marketplace.
    """

    async def _run():
        manager = CredentialsManager()
        admin_key = key or manager.load_api_key()
        auth_token = manager.load_token()

        if not admin_key and not auth_token:
            console.print("[red]Error: Authentication required.[/red]")
            console.print(
                "Please login using 'baselith plugin marketplace login' or provide an API key via --key."
            )
            return

        console.print(f"[cyan]Publishing {path} to marketplace...[/cyan]")
        publisher = PluginPublisher()
        result = await publisher.publish(
            path, admin_key=admin_key, auth_token=auth_token
        )

        if result.get("status") == "success":
            name = result.get("data", {}).get("name", "Plugin")
            version = result.get("data", {}).get("version", "Unknown")
            console.print(
                f"[bold green]Successfully published {name} v{version}![/bold green]"
            )
        else:
            console.print(
                f"[red]Publication failed: {result.get('message', 'Unknown error')}[/red]"
            )
            if "issues" in result:
                for issue in result["issues"]:
                    console.print(f"[yellow]- {issue}[/yellow]")

    asyncio.run(_run())
