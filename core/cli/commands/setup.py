"""Local setup orchestration commands."""

import json
import subprocess
import time

from rich.table import Table

from core.cli.commands.db import cmd_migrate
from core.cli.commands.doctor import run_doctor
from core.cli.commands.doctor_checks import (
    check_docker,
    check_postgres,
    check_qdrant,
    check_redis,
)
from core.cli.commands.env_profiles import ensure_dev_env, ensure_docker_core_env
from core.cli.ui import console, print_header, print_success, print_warning


def run_setup(
    profile: str = "dev",
    install_deps: bool = False,
    include_plugins: bool = False,
    start_services: bool = False,
    migrate: bool = False,
    wait_timeout: int = 60,
    json_output: bool = False,
) -> int:
    """Run a profile-oriented setup flow."""
    if profile == "docker-core":
        return _run_docker_core_setup(json_output=json_output)

    if json_output:
        print(
            json.dumps(
                {
                    "status": "error",
                    "message": "JSON output is not supported for setup orchestration yet.",
                }
            )
        )
        return 1

    if profile != "dev":
        message = f"Unknown setup profile: {profile}"
        print_warning(message)
        return 1

    print_header("🛠️ Baselith Setup", "Developer profile")

    env_changes = ensure_dev_env()
    if env_changes:
        console.print(
            "[green]Updated .env keys:[/green] " + ", ".join(sorted(env_changes))
        )

    services_code = _start_core_services() if start_services else 0
    ready_code = 0
    if start_services and services_code == 0:
        ready_code = _wait_for_core_services(wait_timeout)
    elif start_services:
        ready_code = 1

    can_migrate = not start_services or (services_code == 0 and ready_code == 0)
    migrate_code = cmd_migrate(json_output=False) if migrate and can_migrate else 0
    if migrate and not can_migrate:
        migrate_code = 1
    doctor_code = run_doctor(
        json_output=json_output, fix=True, include_plugins=include_plugins
    )
    deps_code = 0
    if include_plugins or install_deps:
        from core.cli.commands.plugin.deps import deps_install_all

        deps_code = deps_install_all(yes=True, dry_run=not install_deps)

    table = Table(show_header=True, header_style="bold magenta", expand=True)
    table.add_column("Step", style="cyan")
    table.add_column("Result")
    table.add_row(".env dev profile", "updated" if env_changes else "already aligned")
    table.add_row(
        "core services",
        "started"
        if start_services and services_code == 0
        else "needs attention"
        if start_services
        else "skipped",
    )
    table.add_row(
        "service readiness",
        "ready"
        if start_services and ready_code == 0
        else "needs attention"
        if start_services
        else "skipped",
    )
    table.add_row(
        "db migrate",
        "applied"
        if migrate and migrate_code == 0
        else "needs attention"
        if migrate
        else "skipped",
    )
    table.add_row("doctor --fix", "passed" if doctor_code == 0 else "needs attention")
    table.add_row(
        "plugin deps",
        "installed"
        if install_deps
        else "dry-run only"
        if include_plugins
        else "skipped",
    )
    console.print()
    console.print(table)
    console.print()
    if not start_services:
        print_warning(
            "Start Docker Desktop, then run: docker compose up -d postgres redis qdrant"
        )
    if not migrate:
        print_warning("After PostgreSQL is reachable, run: baselith db migrate")
    setup_ok = (
        services_code == 0
        and ready_code == 0
        and migrate_code == 0
        and doctor_code == 0
        and deps_code == 0
    )
    if setup_ok:
        print_success("Setup flow completed.")
    else:
        print_warning("Setup check completed with items that need attention.")
    return 0 if setup_ok else 1


def _start_core_services() -> int:
    """Start local core backing services with Docker Compose."""
    docker = check_docker()
    if not docker.passed:
        print_warning(docker.message)
        if docker.details:
            console.print(f"[dim]{docker.details}[/dim]")
        return 1

    command = ["docker", "compose", "up", "-d", "postgres", "redis", "qdrant"]
    console.print("[cyan]Starting core services:[/cyan] postgres, redis, qdrant")
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.stdout:
        console.print(result.stdout.rstrip())
    if result.stderr and result.returncode != 0:
        console.print(result.stderr.rstrip(), style="red")
    if result.returncode == 0:
        print_success("Core services are starting.")
    else:
        print_warning("Could not start core services.")
    return result.returncode


def _run_docker_core_setup(json_output: bool = False) -> int:
    """Prepare local and Docker env files for the core Docker stack."""
    local_changed = ensure_dev_env()
    docker_changed = ensure_docker_core_env()
    env_path = "configs/.env.docker.core"

    if json_output:
        print(
            json.dumps(
                {
                    "status": "ok",
                    "env_file": env_path,
                    "local_env_file": ".env",
                    "changed_keys": sorted(docker_changed),
                    "local_changed_keys": sorted(local_changed),
                }
            )
        )
        return 0

    print_header("🧱 Baselith Docker Core Setup", "Core-only Docker profile")
    table = Table(show_header=True, header_style="bold magenta", expand=True)
    table.add_column("File", style="cyan")
    table.add_column("Result")
    table.add_row(
        ".env",
        "updated: " + ", ".join(sorted(local_changed))
        if local_changed
        else "already aligned",
    )
    table.add_row(
        env_path,
        "updated: " + ", ".join(sorted(docker_changed))
        if docker_changed
        else "already aligned",
    )
    console.print(table)
    console.print()
    if local_changed:
        console.print(
            "[green]Local CLI env prepared:[/green] " + ", ".join(sorted(local_changed))
        )
    if docker_changed:
        console.print(
            "[green]Docker env prepared:[/green] " + ", ".join(sorted(docker_changed))
        )
    if not local_changed and not docker_changed:
        console.print("[green]Local and Docker env files are already aligned.[/green]")
    console.print()
    console.print("[cyan]Next command:[/cyan]")
    console.print(
        "BASELITH_DOCKER_ENV_FILE=configs/.env.docker.core "
        "docker compose --env-file configs/.env.docker.core "
        "-f docker-compose.core.yml up --build"
    )
    return 0


def _wait_for_core_services(timeout_seconds: int = 60) -> int:
    """Wait until core backing services accept connections."""
    deadline = time.monotonic() + timeout_seconds
    checks = [check_postgres, check_redis, check_qdrant]
    last_failures = []

    console.print("[cyan]Waiting for core services to become ready...[/cyan]")
    while time.monotonic() < deadline:
        results = [check() for check in checks]
        last_failures = [result for result in results if not result.passed]
        if not last_failures:
            print_success("Core services are ready.")
            return 0
        time.sleep(2)

    table = Table(show_header=True, header_style="bold magenta", expand=True)
    table.add_column("Service", style="cyan")
    table.add_column("Problem")
    table.add_column("Resolution", style="dim")
    for failure in last_failures:
        table.add_row(failure.name, failure.message, failure.details)
    console.print(table)
    print_warning("Core services did not become ready before the timeout.")
    return 1


def register_parser(subparsers, formatter_class):
    """Register 'setup' command parser."""
    setup_parser = subparsers.add_parser(
        "setup",
        help="Prepare a BaselithCore environment",
        description="Run profile-oriented setup orchestration for local development.",
        formatter_class=formatter_class,
    )
    setup_parser.add_argument(
        "profile",
        nargs="?",
        default="dev",
        choices=["dev", "docker-core"],
        help="Setup profile to run",
    )
    setup_parser.add_argument(
        "--install-deps",
        action="store_true",
        help="Install missing plugin Python dependencies instead of dry-run",
    )
    setup_parser.add_argument(
        "--with-plugins",
        action="store_true",
        help="Include local plugin checks in the setup flow",
    )
    setup_parser.add_argument(
        "--start-services",
        action="store_true",
        help="Start PostgreSQL, Redis, and Qdrant with Docker Compose",
    )
    setup_parser.add_argument(
        "--migrate",
        action="store_true",
        help="Apply database migrations after services are available",
    )
    setup_parser.add_argument(
        "--wait-timeout",
        type=int,
        default=60,
        help="Seconds to wait for Docker services to become ready",
    )
    setup_parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Emit machine-readable JSON output",
    )
    return setup_parser


__all__ = ["register_parser", "run_setup"]
