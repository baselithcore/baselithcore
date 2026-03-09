"""
Server Run Command.

Provides the CLI entry point for launching the Baselith-Core development server
behind a Uvicorn instance with auto-reload capabilities.
"""

from typing import Any
from pathlib import Path

from rich.panel import Panel
from rich.table import Table
from core.cli.ui import console, print_error


def run_server(
    host: str = "0.0.0.0",  # nosec B104
    port: int = 8000,
    reload: bool = True,
    workers: int = 1,
    log_level: str = "info",
) -> int:
    """
    Start the development server using uvicorn.

    Args:
        host: Host to bind to
        port: Port to listen on
        reload: Enable auto-reload on file changes
        workers: Number of worker processes (ignored if reload=True)
        log_level: Logging level

    Returns:
        Exit code (0 for success)
    """
    import os
    import sys

    # Ensure the current directory is in sys.path so backend:app can be found
    if os.getcwd() not in sys.path:
        sys.path.insert(0, os.getcwd())

    try:
        import uvicorn
    except ImportError:
        print_error("uvicorn is not installed", "Run: pip install uvicorn")
        return 1

    # Check if backend.py exists
    backend_path = Path.cwd() / "backend.py"
    if not backend_path.exists():
        print_error(
            "backend.py not found in current directory",
            "Make sure you're in the project root",
        )
        return 1

    table = Table(show_header=False, expand=False, box=None)
    table.add_column("Property", style="bold cyan")
    table.add_column("Value", style="none")

    table.add_row("Host", host)
    table.add_row("Port", str(port))
    table.add_row(
        "Reload", "[green]enabled[/green]" if reload else "[dim]disabled[/dim]"
    )
    table.add_row(
        "Workers", str(workers) if not reload else "1 [dim](reload mode)[/dim]"
    )

    local_url = f"http://{host if host != '0.0.0.0' else 'localhost'}:{port}"  # nosec B104
    table.add_row("API Docs", f"[link={local_url}/docs]{local_url}/docs[/link]")
    table.add_row("Console", f"[link={local_url}/console]{local_url}/console[/link]")

    panel = Panel(
        table,
        title="[bold green]🚀 Baselith-Core Server Starting[/bold green]",
        border_style="green",
        expand=False,
    )
    console.print()
    console.print(panel)
    console.print()

    try:
        # Configure uvicorn
        config: dict[str, Any] = {
            "app": "backend:app",
            "host": host,
            "port": port,
            "reload": reload,
            "log_level": log_level,
            "access_log": True,
        }

        # Only set workers if not in reload mode
        if not reload and workers > 1:
            config["workers"] = workers

        uvicorn.run(**config)
        return 0

    except KeyboardInterrupt:
        console.print("\n[yellow]✋ Server stopped.[/yellow]")
        return 0
    except Exception as e:
        print_error(f"Error starting server: {e}")
        return 1


def register_parser(subparsers, formatter_class):
    """Register 'run' command parser."""
    run_parser = subparsers.add_parser(
        "run",
        help="Start the development server",
        description="Launch the FastAPI application with Uvicorn, featuring auto-reload and professional logging.",
        formatter_class=formatter_class,
    )
    run_parser.add_argument(
        "--host",
        default="0.0.0.0",  # nosec B104
        help="Network interface to bind the server to (default: 0.0.0.0)",
    )
    run_parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Network port to listen on (default: 8000)",
    )
    run_parser.add_argument(
        "--reload",
        action="store_true",
        default=True,
        help="Enable hot-reloading for rapid development (default: True)",
    )
    run_parser.add_argument(
        "--no-reload",
        action="store_true",
        help="Disable hot-reloading (production-like behavior)",
    )
    run_parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of parallel worker processes (ignored with --reload)",
    )
    run_parser.add_argument(
        "--log-level",
        choices=["debug", "info", "warning", "error"],
        default="info",
        help="Set the verbosity of system logs (default: info)",
    )
    return run_parser


__all__ = ["run_server", "register_parser"]
