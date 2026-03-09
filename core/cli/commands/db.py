"""
Database and VectorStore utility commands.
"""

from rich.table import Table
from rich.prompt import Confirm
from core.cli.ui import console, print_header, print_success, print_error, print_warning


import json


def cmd_status(json_output: bool = False) -> int:
    """Show status of all persistent stores."""
    from core.cli.commands.doctor import (
        check_redis,
        check_qdrant,
        check_graph_db,
        check_postgres,
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
            for coll in collections:
                client.delete_collection(coll.name)
            print_success(f"Cleared {len(collections)} Qdrant collections.")
    except Exception as e:
        if json_output:
            print(
                json.dumps(
                    {"status": "error", "message": f"Failed to reset Qdrant: {str(e)}"}
                )
            )
        else:
            print_error("Failed to reset Qdrant", str(e))

    # Reset Redis
    try:
        from core.config import get_storage_config
        import redis

        storage_config = get_storage_config()
        r = redis.Redis.from_url(storage_config.cache_redis_url)
        r.flushall()
        if not json_output:
            print_success("Flushed Redis cache.")
    except Exception as e:
        if json_output:
            print(
                json.dumps(
                    {"status": "error", "message": f"Failed to reset Redis: {str(e)}"}
                )
            )
        else:
            print_error("Failed to reset Redis", str(e))

    if json_output:
        print(json.dumps({"status": "ok", "message": "Databases reset successfully"}))
    return 0


def run_db(command: str, json_output: bool = False) -> int:
    """Main entrypoint for db commands."""
    if command == "status":
        return cmd_status(json_output=json_output)
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
    db_subparsers = db_parser.add_subparsers(
        dest="db_command", title="Database Operations"
    )
    db_subparsers.add_parser(
        "status",
        help="Check connectivity and migration status for all databases",
        formatter_class=formatter_class,
    )
    db_subparsers.add_parser(
        "reset",
        help="Wipe all data and reset schemas (DEVELOPMENT ONLY)",
        formatter_class=formatter_class,
    )
    return db_parser


__all__ = ["run_db", "register_parser"]
