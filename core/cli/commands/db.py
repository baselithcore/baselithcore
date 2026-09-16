"""
Database and VectorStore utility commands.
"""

import json
import subprocess
import sys
from pathlib import Path

from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Confirm
from rich.table import Table

from core.cli.ui import console, print_error, print_header, print_success, print_warning


def cmd_status(json_output: bool = False) -> int:
    """Show status of all persistent stores."""
    from core.cli.commands.doctor import (
        check_graph_db,
        check_postgres,
        check_qdrant,
        check_redis,
    )

    if not json_output:
        print_header("📊 Database Status", "Baselith-Core Data Stores")

    if not json_output:
        with console.status("[bold blue]Checking databases...", spinner="dots"):
            checks = [
                check_redis(),
                check_qdrant(),
                check_postgres(),
                check_graph_db(),
            ]
    else:
        checks = [
            check_redis(),
            check_qdrant(),
            check_postgres(),
            check_graph_db(),
        ]

    if json_output:
        json_result = []
        for check in checks:
            json_result.append(
                {
                    "database": check.name,
                    "status": "online" if check.passed else "offline",
                    "message": check.message,
                    "details": check.details,
                }
            )
        print(json.dumps({"status": "ok", "databases": json_result}))
        return 0

    table = Table(show_header=True, header_style="bold magenta", expand=True)
    table.add_column("Status", style="dim", width=8, justify="center")
    table.add_column("Datastore")
    table.add_column("Message")
    table.add_column("Details", style="dim")

    for check in checks:
        if check.passed:
            status = "[green]✅ ON[/green]"
        else:
            status = "[red]❌ OFF[/red]"
        table.add_row(status, check.name, check.message, check.details)

    console.print(table)
    return 0


def cmd_reset(json_output: bool = False) -> int:
    """Reset vector stores and cache databases."""
    if not json_output:
        print_header("⚠️ Database Reset", "Clear all data in Vector Stores and Cache")

        print_warning(
            "This action cannot be undone. All embeddings and cache will be lost!"
        )

        if not Confirm.ask("Are you sure you want to completely reset all databases?"):
            console.print("[yellow]Reset cancelled.[/yellow]")
            return 0

    # Reset Qdrant
    try:
        from core.config import get_vectorstore_config

        v_config = get_vectorstore_config()
        if v_config.provider == "qdrant":
            from qdrant_client import QdrantClient

            client = QdrantClient(host=v_config.host, port=v_config.port)
            collections = client.get_collections().collections

            if json_output:
                for coll in collections:
                    client.delete_collection(coll.name)
            else:
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    console=console,
                    transient=True,
                ) as progress:
                    task = progress.add_task(
                        f"[bold green]Clearing {len(collections)} Qdrant collections...",
                        total=len(collections),
                    )
                    for coll in collections:
                        client.delete_collection(coll.name)
                        progress.advance(task)

            if not json_output:
                print_success(f"Cleared {len(collections)} Qdrant collections.")
    except Exception as e:
        if json_output:
            print(
                json.dumps(
                    {"status": "error", "message": f"Failed to reset Qdrant: {e!s}"}
                )
            )
        else:
            print_error("Failed to reset Qdrant", str(e))

    # Reset Redis
    try:
        import redis

        from core.config import get_storage_config

        storage_config = get_storage_config()
        r = redis.Redis.from_url(storage_config.cache_redis_url)

        if json_output:
            r.flushdb()
        else:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
                transient=True,
            ) as progress:
                progress.add_task("[bold green]Flushing Redis cache...", total=None)
                r.flushdb()
            print_success("Flushed Redis cache.")
    except Exception as e:
        if json_output:
            print(
                json.dumps(
                    {"status": "error", "message": f"Failed to reset Redis: {e!s}"}
                )
            )
        else:
            print_error("Failed to reset Redis", str(e))

    if json_output:
        print(json.dumps({"status": "ok", "message": "Databases reset successfully"}))
    return 0


def cmd_migrate(json_output: bool = False) -> int:
    """Apply Alembic migrations to the configured PostgreSQL database."""
    from core.cli.commands.doctor import check_postgres

    alembic_ini = Path.cwd() / "alembic.ini"
    if not alembic_ini.exists():
        message = "alembic.ini not found"
        if json_output:
            print(json.dumps({"status": "error", "message": message}))
        else:
            print_error("Cannot run migrations", message)
        return 1

    postgres = check_postgres()
    if not postgres.passed:
        if json_output:
            print(
                json.dumps(
                    {
                        "status": "error",
                        "message": postgres.message,
                        "details": postgres.details,
                    }
                )
            )
        else:
            print_error("PostgreSQL is not ready", postgres.message)
            if postgres.details:
                console.print(f"[dim]{postgres.details}[/dim]")
        return 1

    cmd = [sys.executable, "-m", "alembic", "upgrade", "head"]
    if not json_output:
        print_header("🧱 Database Migrations", "Alembic upgrade head")

    result = subprocess.run(
        cmd,
        cwd=Path.cwd(),
        capture_output=True,
        text=True,
        check=False,
    )

    if json_output:
        print(
            json.dumps(
                {
                    "status": "ok" if result.returncode == 0 else "error",
                    "command": cmd,
                    "returncode": result.returncode,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                }
            )
        )
        return result.returncode

    if result.stdout:
        console.print(result.stdout.rstrip())
    if result.stderr:
        console.print(result.stderr.rstrip(), style="red")

    if result.returncode == 0:
        print_success("Database migrations applied.")
    else:
        print_error("Database migrations failed", f"Exit code {result.returncode}")
    return result.returncode


def run_db(command: str, json_output: bool = False) -> int:
    """Main entrypoint for db commands."""
    if command == "status":
        return cmd_status(json_output=json_output)
    elif command == "migrate":
        return cmd_migrate(json_output=json_output)
    elif command == "reset":
        return cmd_reset(json_output=json_output)
    else:
        if json_output:
            print(
                json.dumps(
                    {"status": "error", "message": f"Unknown db command: {command}"}
                )
            )
        else:
            print_error("Unknown db command", command)
        return 1


def register_parser(subparsers, formatter_class):
    """Register 'db' command parser."""
    db_parser = subparsers.add_parser(
        "db",
        help="Manage database systems",
        description="Manage persistence layers, including SQL databases and VectorStores used for RAG and memory.",
        formatter_class=formatter_class,
    )
    db_parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Emit machine-readable JSON output",
    )
    db_subparsers = db_parser.add_subparsers(
        dest="db_command", title="Database Operations"
    )
    status_parser = db_subparsers.add_parser(
        "status",
        help="Check connectivity and migration status for all databases",
        formatter_class=formatter_class,
    )
    reset_parser = db_subparsers.add_parser(
        "reset",
        help="Wipe all data and reset schemas (DEVELOPMENT ONLY)",
        formatter_class=formatter_class,
    )
    migrate_parser = db_subparsers.add_parser(
        "migrate",
        help="Apply PostgreSQL schema migrations with Alembic",
        formatter_class=formatter_class,
    )
    for subcommand in (status_parser, reset_parser, migrate_parser):
        subcommand.add_argument(
            "--json",
            action="store_true",
            default=False,
            help="Emit machine-readable JSON output",
        )
    return db_parser


__all__ = ["cmd_migrate", "register_parser", "run_db"]
