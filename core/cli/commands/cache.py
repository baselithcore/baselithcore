"""
Cache utility commands.
"""

from rich.table import Table
from rich.prompt import Confirm
from rich.progress import Progress, SpinnerColumn, TextColumn
from core.cli.ui import console, print_header, print_success, print_error, print_warning


import json


def cmd_stats(json_output: bool = False) -> int:
    """Show Redis cache usage statistics."""
    if not json_output:
        print_header("📦 Cache Statistics", "Redis Database Memory Info")

    try:
        from core.config import get_storage_config
        import redis

        config = get_storage_config()
        r = redis.Redis.from_url(config.cache_redis_url)

        info = r.info("memory")
        keys = r.dbsize()

        if json_output:
            print(
                json.dumps(
                    {
                        "status": "ok",
                        "total_keys": keys,
                        "used_memory_human": info.get("used_memory_human", "N/A"),
                        "peak_memory_human": info.get("used_memory_peak_human", "N/A"),
                        "fragmentation_ratio": info.get(
                            "mem_fragmentation_ratio", "N/A"
                        ),
                    }
                )
            )
            return 0

        table = Table(show_header=True, header_style="bold magenta", expand=True)
        table.add_column("Metric", style="cyan")
        table.add_column("Value")

        table.add_row("Total Keys", str(keys))
        table.add_row("Used Memory (Human)", info.get("used_memory_human", "N/A"))
        table.add_row("Peak Memory (Human)", info.get("used_memory_peak_human", "N/A"))
        table.add_row(
            "Memory Fragmentation", str(info.get("mem_fragmentation_ratio", "N/A"))
        )

        console.print(table)
        return 0
    except ImportError:
        if json_output:
            print(
                json.dumps(
                    {"status": "error", "message": "Redis package not installed"}
                )
            )
        else:
            print_error("Redis package not installed. Run: pip install redis")
        return 1
    except Exception as e:
        if json_output:
            print(json.dumps({"status": "error", "message": str(e)}))
        else:
            print_error("Failed to connect to Redis Cache", str(e))
        return 1


def cmd_clear(json_output: bool = False) -> int:
    """Clear Redis cache database."""
    if not json_output:
        print_header("🗑️ Clear Cache", "Flush Redis Database Cache")
        print_warning(
            "This operation is irreversible and may cause temporary performance degradation."
        )

        if not Confirm.ask("Are you sure you want to completely clear the cache?"):
            console.print("[yellow]Operation cancelled.[/yellow]")
            return 0

    try:
        from core.config import get_storage_config
        import redis

        config = get_storage_config()
        r = redis.Redis.from_url(config.cache_redis_url)

        # We flush the selected DB only
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

        if json_output:
            print(
                json.dumps(
                    {"status": "ok", "message": "Redis cache flushed successfully."}
                )
            )
        else:
            print_success("Redis cache flushed successfully.")
        return 0
    except Exception as e:
        if json_output:
            print(json.dumps({"status": "error", "message": str(e)}))
        else:
            print_error("Failed to clear Redis Cache", str(e))
        return 1


def run_cache(command: str, json_output: bool = False) -> int:
    """Main entrypoint for cache commands."""
    if command == "stats":
        return cmd_stats(json_output=json_output)
    elif command == "clear":
        return cmd_clear(json_output=json_output)
    else:
        if json_output:
            print(
                json.dumps(
                    {"status": "error", "message": f"Unknown cache command: {command}"}
                )
            )
        else:
            print_error("Unknown cache command", command)
        return 1


def register_parser(subparsers, formatter_class):
    """Register 'cache' command parser."""
    cache_parser = subparsers.add_parser(
        "cache",
        help="Manage Redis cache",
        description="Interact with the Redis-backed cache layer used for session storage and performance optimization.",
        formatter_class=formatter_class,
    )
    cache_subparsers = cache_parser.add_subparsers(
        dest="cache_command", title="Cache Operations"
    )
    cache_subparsers.add_parser(
        "stats",
        help="View real-time Redis memory usage and hit/miss metrics",
        formatter_class=formatter_class,
    )
    cache_subparsers.add_parser(
        "clear",
        help="Flush all keys from the current Redis cache database",
        formatter_class=formatter_class,
    )
    return cache_parser


__all__ = ["run_cache", "register_parser"]
