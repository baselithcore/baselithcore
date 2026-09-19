"""
Server Run Command.

Provides the CLI entry point for launching the Baselith-Core development server
behind a Uvicorn instance with auto-reload capabilities.
"""

import argparse
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rich.panel import Panel
from rich.table import Table

from core.cli.ui import console, print_error

if TYPE_CHECKING:
    from core.cli.commands.doctor_checks import CheckResult


def run_server(
    host: str | None = None,  # nosec B104
    port: int | None = None,
    reload: bool = True,
    workers: int = 1,
    log_level: str = "info",
    preflight: bool = True,
    include_plugins: bool = False,
    require_services: bool = False,
) -> int:
    """
    Start the development server using uvicorn.

    Args:
        host: Host to bind to (defaults to app config when None)
        port: Port to listen on (defaults to app config when None)
        reload: Enable auto-reload on file changes
        workers: Number of worker processes (ignored if reload=True)
        log_level: Logging level
        preflight: Run doctor checks before starting Uvicorn
        include_plugins: Include plugin readiness checks in preflight
        require_services: Block startup when backing services are unreachable

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

    if preflight:
        preflight_code = _run_preflight(
            include_plugins=include_plugins,
            require_services=require_services,
        )
        if preflight_code != 0:
            return preflight_code

    # Resolve host/port from app config lazily — keeps the heavy config import
    # out of module load (and CLI startup/registration), so `baselith --help`
    # and tab-completion stay fast.
    if host is None or port is None:
        from core.config import get_app_config

        _cfg = get_app_config()
        if host is None:
            host = _cfg.host  # nosec B104
        if port is None:
            port = _cfg.port

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

    local_url = f"http://{host if host != '0.0.0.0' else 'localhost'}:{port}"  # nosec B104  # noqa: S104
    # Emit bare URLs (no Rich ``[link=...]`` OSC 8 markup). Many terminals do
    # not render OSC 8 hyperlinks as clickable and the escape sequence also
    # defeats their native URL auto-detection, leaving the links dead. Plain
    # underlined URLs let the terminal's own detection make them cmd-clickable.
    table.add_row("API Docs", f"[underline cyan]{local_url}/docs[/underline cyan]")
    table.add_row("Console", f"[underline cyan]{local_url}/console[/underline cyan]")

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
        # Configure uvicorn.
        #
        # The three settings below are not development conveniences: they are
        # the parity this command owed `backend.py` and the container CMD in
        # the Dockerfile, which both already set them. Without them `baselith
        # run` behind any proxy — the compose gateway, a local ingress, a
        # tunnel — reports the proxy as `request.client.host` for every caller,
        # which collapses the per-IP rate limiter, the failed-auth throttle and
        # the admin lockout into ONE shared bucket, and a Ctrl-C with open SSE
        # streams cuts the lifespan teardown short.
        #
        # Trust stays limited to FORWARDED_ALLOW_IPS (uvicorn's own default is
        # 127.0.0.1): widen it to the proxy's address, never to "*", or the
        # header becomes caller-controlled and the limiter is bypassed by
        # spoofing it.
        config: dict[str, Any] = {
            "app": "backend:app",
            "host": host,
            "port": port,
            "reload": reload,
            "log_level": log_level,
            "access_log": True,
            "proxy_headers": True,
            "forwarded_allow_ips": os.getenv("FORWARDED_ALLOW_IPS", "127.0.0.1"),
            "timeout_graceful_shutdown": int(
                os.getenv("GRACEFUL_SHUTDOWN_TIMEOUT", "30")
            ),
            # Parity with the container CMD and backend.py for the two
            # remaining runtime knobs: a keep-alive longer than the proxy's
            # upstream idle timeout (uvicorn's 5s default is shorter than
            # nginx/ALB/Envoy's 60s, which surfaces as sporadic 502s), and
            # optional load-shedding (503 above N concurrent connections
            # instead of queueing; unset = uvicorn's default, no limit).
            "timeout_keep_alive": int(os.getenv("UVICORN_KEEP_ALIVE", "75")),
        }
        limit_concurrency = os.getenv("UVICORN_LIMIT_CONCURRENCY", "").strip()
        if limit_concurrency:
            config["limit_concurrency"] = int(limit_concurrency)

        # Only set workers if not in reload mode
        if not reload and workers > 1:
            config["workers"] = workers

        # Children inherit this and can tell they are one of several
        # processes — the only way for a plugin to know that request-spanning
        # state cannot live in its own memory.
        from core.config.concurrency import set_web_concurrency

        set_web_concurrency(workers if not reload else 1)

        uvicorn.run(**config)
        return 0

    except KeyboardInterrupt:
        console.print("\n[yellow]✋ Server stopped.[/yellow]")
        return 0
    except Exception as e:
        print_error(f"Error starting server: {e}")
        return 1


CONNECTIVITY_CHECKS = {
    "Redis (Cache)",
    "Qdrant",
    "PostgreSQL",
    "GraphDB",
    "Telemetry",
}


def _run_preflight(
    include_plugins: bool = False,
    require_services: bool = False,
) -> int:
    """Run startup diagnostics before Uvicorn starts."""
    from core.cli.commands.doctor import run_checks
    from core.cli.commands.doctor_checks import ensure_data_dirs

    # Repair what the diagnostic would only tell us to repair by hand: a data
    # directory that does not exist yet is a mkdir, not a reason to refuse to
    # boot. Left fatal it is an endless restart loop under systemd. Anything
    # that cannot be created still fails the check below.
    created = ensure_data_dirs()
    if created:
        console.print(
            f"[dim]Created {len(created)} missing data director(y/ies): "
            f"{', '.join(str(path) for path in created)}[/dim]"
        )
    checks = run_checks(include_plugins=include_plugins)
    failed_checks = [
        check for check in checks if not check.passed and check.severity == "fail"
    ]
    service_failures = [
        check for check in failed_checks if _is_connectivity_check(check)
    ]
    failures = [
        check
        for check in failed_checks
        if require_services or not _is_connectivity_check(check)
    ]
    if service_failures and not require_services:
        _print_preflight_panel(
            service_failures,
            title="[bold yellow]Startup preflight service warnings[/bold yellow]",
            border_style="yellow",
        )
        console.print(
            "[dim]Service reachability is handled by application readiness and "
            "lazy initialization. Use `baselith run --require-services` to block "
            "startup on these checks.[/dim]"
        )
    if not failures:
        return 0

    _print_preflight_panel(
        failures,
        title="[bold red]Startup preflight failed[/bold red]",
        border_style="red",
    )
    console.print("[dim]Run `baselith doctor` for the full diagnostic report.[/dim]")
    console.print(
        "[dim]Use `baselith run --check-plugins` to include plugin readiness.[/dim]"
    )
    console.print("[dim]Use `baselith run --skip-preflight` only for debugging.[/dim]")
    return 1


def _is_connectivity_check(check: "CheckResult") -> bool:
    if check.name in CONNECTIVITY_CHECKS:
        return True
    if check.name != "LLM Provider":
        return False
    message = check.message.lower()
    return "not reachable" in message or "cannot connect" in message


def _print_preflight_panel(
    failures: "Sequence[CheckResult]",
    title: str,
    border_style: str,
) -> None:
    table = Table(show_header=True, header_style="bold magenta", expand=True)
    table.add_column("Component", style="cyan")
    table.add_column("Problem")
    table.add_column("Resolution", style="dim")
    for check in failures:
        table.add_row(check.name, check.message, check.details)

    console.print()
    console.print(
        Panel(
            table,
            title=title,
            border_style=border_style,
            expand=False,
        )
    )


def register_parser(
    subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]",
    formatter_class: type[argparse.HelpFormatter],
) -> argparse.ArgumentParser:
    """Register 'run' command parser."""
    run_parser = subparsers.add_parser(
        "run",
        help="Start the development server",
        description="Launch the FastAPI application with Uvicorn, featuring auto-reload and professional logging.",
        formatter_class=formatter_class,
    )
    # Defaults are None so the app config is only loaded when ``run`` actually
    # executes (resolved in ``run_server``), keeping CLI startup fast.
    run_parser.add_argument(
        "--host",
        default=None,  # nosec B104
        help="Network interface to bind the server to (default: from app config)",
    )
    run_parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Network port to listen on (default: from app config)",
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
    run_parser.add_argument(
        "--skip-preflight",
        action="store_true",
        help="Start Uvicorn without running doctor checks first",
    )
    run_parser.add_argument(
        "--check-plugins",
        action="store_true",
        help="Include plugin readiness checks in the startup preflight",
    )
    run_parser.add_argument(
        "--require-services",
        action="store_true",
        help="Block startup when preflight cannot reach backing services",
    )
    return run_parser


__all__ = ["register_parser", "run_server"]
