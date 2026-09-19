"""
Marketplace Management CLI Commands.

Provides commands to discover, search, and install plugins from the
Baselith Marketplace.
"""

import asyncio
import os

from rich.console import Console
from rich.table import Table

from core.marketplace import PluginCategory, PluginInstaller, PluginRegistry
from core.marketplace.auth import AuthService, CredentialsManager
from core.marketplace.publisher import PluginPublisher

console = Console()


def _check_marketplace() -> bool:
    """Check if the marketplace system is fully available."""
    try:
        from core.marketplace.registry import PluginRegistry  # noqa: F401

        return True
    except (ImportError, ModuleNotFoundError):
        return False


def search_plugins(
    query: str | None = None, category: str = "all", force_refresh: bool = False
) -> int:
    """
    Search for plugins in the Baselith Marketplace.

    Returns:
        ``0`` on success, ``1`` on a bad category or a registry error. A search
        that simply matches nothing is a success: the query ran and the answer
        is "none", which is not a failure to report to a shell.
    """

    async def _run() -> int:
        registry = PluginRegistry()

        try:
            cat = PluginCategory(category.lower())
        except ValueError:
            console.print(f"[red]Error: Invalid category '{category}'.[/red]")
            return 1

        console.print("[cyan]Searching marketplace...[/cyan]")

        try:
            plugins = await registry.search(
                query=query, category=cat, force=force_refresh
            )

            if not plugins:
                console.print(
                    "[yellow]No plugins found matching your criteria.[/yellow]"
                )
                return 0

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
            return 0

        except Exception as e:
            console.print(f"[red]Error searching marketplace: {e}[/red]")
            return 1

    return asyncio.run(_run())


def info_plugin(plugin_id: str) -> int:
    """
    Show detailed information about a marketplace plugin.

    Returns:
        ``0`` when the plugin exists, ``1`` when it is not in the marketplace.
    """

    async def _run() -> int:
        registry = PluginRegistry()
        plugin = await registry.get_plugin(plugin_id)

        if not plugin:
            console.print(
                f"[red]Error: Plugin '{plugin_id}' not found in marketplace.[/red]"
            )
            return 1

        console.print(f"[bold green]Plugin: {plugin.name}[/bold green] ({plugin.id})")
        console.print(f"Status: [magenta]{plugin.status.value}[/magenta]")
        console.print(f"Author: [cyan]{plugin.author}[/cyan]")
        console.print(f"Description: {plugin.description}")
        if plugin.git_url:
            console.print(f"Repository: [blue]{plugin.git_url}[/blue]")
        if plugin.tags:
            console.print(f"Tags: [yellow]{', '.join(plugin.tags)}[/yellow]")
        console.print(f"Stars: {plugin.stars} | Downloads: {plugin.downloads}")
        return 0

    return asyncio.run(_run())


def install_plugin_cmd(
    plugin_id: str, version: str | None = None, force: bool = False
) -> int:
    """
    Install a plugin from the marketplace.

    Returns:
        ``0`` when the plugin is installed or was already installed, ``1``
        when it is unknown to the marketplace or the install failed.
    """

    async def _run() -> int:
        registry = PluginRegistry()
        installer = PluginInstaller()

        plugin = await registry.get_plugin(plugin_id)
        if not plugin:
            console.print(
                f"[red]Error: Plugin '{plugin_id}' not found in marketplace.[/red]"
            )
            return 1

        console.print(f"[cyan]Installing {plugin.name}...[/cyan]")

        # Use version if provided, otherwise 'main'
        branch = version or "main"
        result = await installer.install(plugin, branch=branch, force=force)

        if result.status.value == "success":
            console.print(
                f"[bold green]Successfully installed {plugin.name} to {result.destination}[/bold green]"
            )
            console.print("[dim]Restart Baselith to load the new plugin.[/dim]")
            return 0
        if result.status.value == "already_installed":
            console.print(
                f"[yellow]Plugin {plugin.name} is already installed at {result.destination}.[/yellow]"
            )
            # Already installed is the requested end state, so it succeeds —
            # otherwise a re-run of a provisioning script would fail.
            return 0
        console.print(f"[red]Failed to install {plugin.name}: {result.error}[/red]")
        return 1

    return asyncio.run(_run())


def uninstall_plugin_cmd(plugin_id: str) -> int:
    """
    Uninstall a plugin.

    Returns:
        ``0`` when the plugin was removed, ``1`` when it could not be.
    """

    async def _run() -> int:
        installer = PluginInstaller()

        if await installer.uninstall(plugin_id):
            console.print(
                f"[bold green]Successfully uninstalled {plugin_id}.[/bold green]"
            )
            return 0
        console.print(
            f"[red]Error: Could not uninstall plugin '{plugin_id}'. Ensure the name is correct.[/red]"
        )
        return 1

    return asyncio.run(_run())


def update_plugin_cmd(plugin_id: str) -> int:
    """
    Update an existing plugin from the marketplace.

    Returns:
        ``0`` when the plugin is reinstalled, ``1`` when it is unknown to the
        marketplace or the reinstall failed.
    """

    async def _run() -> int:
        # Simply uninstall and reinstall for now
        installer = PluginInstaller()
        registry = PluginRegistry()

        plugin = await registry.get_plugin(plugin_id)
        if not plugin:
            console.print(
                f"[red]Error: Plugin '{plugin_id}' not found in marketplace.[/red]"
            )
            return 1

        console.print(f"[cyan]Updating {plugin.name}...[/cyan]")
        await installer.uninstall(plugin.name)
        result = await installer.install(plugin)

        if result.status.value == "success":
            console.print(
                f"[bold green]Successfully updated {plugin.name}.[/bold green]"
            )
            return 0
        console.print(f"[red]Failed to update {plugin.name}: {result.error}[/red]")
        return 1

    return asyncio.run(_run())


def login_cmd(github_token: str | None = None) -> int:
    """
    Authenticate with the marketplace.

    Two paths:
      * ``--github-token``: exchange a GitHub token for a marketplace JWT
        automatically (recommended for external publishers).
      * interactive: paste an existing marketplace JWT or API key.

    Returns:
        ``0`` once credentials are stored, ``1`` when the exchange is rejected
        or no credentials were supplied.
    """

    async def _run() -> int:
        # Automated login: exchange a GitHub token for a marketplace session.
        # The GitHub token is used once for the exchange and never stored.
        if github_token:
            console.print(
                "[cyan]Exchanging your GitHub token for a marketplace session...[/cyan]"
            )
            auth_service = AuthService()
            result = await auth_service.login_with_github(github_token)
            if result.get("status") == "success":
                console.print("[bold green]Successfully authenticated.[/bold green]")
                user = result.get("user") or {}
                login = user.get("login") if isinstance(user, dict) else None
                if login:
                    console.print(f"Identity: [cyan]{login}[/cyan]")
                console.print(
                    "[dim]You can now run 'baselith plugin marketplace publish .'[/dim]"
                )
                return 0
            console.print(
                f"[red]Login failed: {result.get('message', 'Unknown error')}[/red]"
            )
            return 1

        console.print("[cyan]Welcome to Baselith Marketplace Authentication.[/cyan]")
        console.print(
            "[dim]Note: A future update will introduce interactive centralized browser login.[/dim]\n"
        )

        auth_input = console.input(
            "Please enter your Marketplace API Key or JWT Token: "
        )
        if not auth_input.strip():
            console.print("[red]Error: Credentials cannot be empty.[/red]")
            return 1

        manager = CredentialsManager()

        # Simple check for JWT structure (header.payload.signature)
        if len(auth_input.split(".")) == 3:
            token = auth_input.strip()
            await manager.save_token(token)
            console.print(
                "[bold green]Successfully saved Authentication Token.[/bold green]"
            )

            # Attempt to sync profile immediately
            auth_service = AuthService()
            if await auth_service.sync_user_profile():
                console.print("[cyan]Verified identity and synced user profile.[/cyan]")
        else:
            await manager.save_api_key(auth_input.strip())
            console.print("[bold green]Successfully saved API Key.[/bold green]")
        return 0

    return asyncio.run(_run())


def logout_cmd() -> int:
    """
    Remove cached marketplace credentials.

    Returns:
        ``0``. Deleting credentials that were not there is not a failure.
    """

    async def _run() -> int:
        manager = CredentialsManager()
        await manager.delete_credentials()
        console.print(
            "[bold green]Successfully logged out. Cached credentials removed.[/bold green]"
        )
        return 0

    return asyncio.run(_run())


def identity_cmd() -> int:
    """
    Show the currently logged-in marketplace identity.

    Returns:
        ``0`` when an identity is established, ``1`` when nothing is stored or
        the stored token no longer verifies. A script asking "am I logged in?"
        needs that answer in the exit status, not only on stdout.
    """

    async def _run() -> int:
        auth_service = AuthService()
        manager = CredentialsManager()

        token = await manager.load_token()
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
                return 0

            console.print(
                f"[yellow]Token found but verification failed: {result.get('message')}[/yellow]"
            )
            console.print("[dim]You may need to login again.[/dim]")
            return 1

        api_key = await manager.load_api_key()
        if api_key:
            console.print(
                "[bold green]Authenticated via API Key (Legacy).[/bold green]"
            )
            console.print(f"Key Prefix: [dim]{api_key[:8]}...[/dim]")
            return 0

        console.print("[yellow]Not authenticated.[/yellow]")
        console.print("Use 'baselith plugin marketplace login' to authenticate.")
        return 1

    return asyncio.run(_run())


def publish_plugin_cmd(path: str, key: str | None = None) -> int:
    """
    Publish a plugin to the marketplace.

    Returns:
        ``0`` when the marketplace accepted the plugin, ``1`` when it rejected
        it or no credentials were available. This is the one that mattered
        most: a rejected publish reporting success is a release that looks
        shipped and is not.
    """

    async def _run() -> int:
        manager = CredentialsManager()
        # Resolution order: --key, MARKETPLACE_API_KEY, stored credentials.
        admin_key = (
            key or os.environ.get("MARKETPLACE_API_KEY") or await manager.load_api_key()
        )
        auth_token = await manager.load_token()

        if not admin_key and not auth_token:
            console.print("[red]Error: Authentication required.[/red]")
            console.print(
                "Please login using 'baselith plugin marketplace login' or provide an API key via --key."
            )
            return 1

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
            return 0

        console.print(
            f"[red]Publication failed: {result.get('message', 'Unknown error')}[/red]"
        )
        if "issues" in result:
            for issue in result["issues"]:
                console.print(f"[yellow]- {issue}[/yellow]")
        return 1

    return asyncio.run(_run())
