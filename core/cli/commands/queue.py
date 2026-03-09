"""
Task Queue utility commands for RQ.
"""

from rich.table import Table
from core.cli.ui import console, print_header, print_error, print_warning


def cmd_status() -> int:
    """Show RQ task queue status."""
    print_header("📋 Queue Status", "Background Task Orchestration")

    try:
        from core.config import get_storage_config
        from redis import Redis
        from rq import Queue, Worker

        config = get_storage_config()
        r = Redis.from_url(config.queue_redis_url)
        queue = Queue("default", connection=r)

        workers = Worker.all(connection=r)

        # We also want to see the status of different job types
        from rq.registry import (
            StartedJobRegistry,
            FinishedJobRegistry,
            FailedJobRegistry,
        )

        started = StartedJobRegistry("default", connection=r).count
        finished = FinishedJobRegistry("default", connection=r).count
        failed = FailedJobRegistry("default", connection=r).count

        table = Table(show_header=True, header_style="bold magenta", expand=True)
        table.add_column("Metric", style="cyan")
        table.add_column("Value")

        table.add_row("Active Workers", str(len(workers)))
        table.add_row("Pending Jobs", str(len(queue)))
        table.add_row("Running Jobs", str(started))
        table.add_row("Completed Jobs", str(finished))
        table.add_row("Failed Jobs", f"[red]{failed}[/red]" if failed > 0 else "0")

        console.print(table)

        if workers:
            console.print("\n[dim]Worker Details:[/dim]")
            for i, w in enumerate(workers):
                state_color = "green" if w.state == "idle" else "yellow"
                console.print(
                    f"  {i + 1}. {w.name} - [{state_color}]{w.state}[/{state_color}]"
                )
        else:
            print_warning("No active workers found. Run 'baselith queue worker'")

        return 0
    except ImportError:
        print_error("RQ or Redis package not installed. Check dependencies.")
        return 1
    except Exception as e:
        print_error("Failed to connect to task queue", str(e))
        return 1


def cmd_worker(concurrency: int = 1) -> int:
    """Start an RQ worker process."""
    print_header("👷 Starting Queue Worker", "Baselith-Core Task Processing")

    try:
        from core.config import get_storage_config
        from redis import Redis
        from rq import Worker, Queue

        config = get_storage_config()
        r = Redis.from_url(config.queue_redis_url)

        # If concurrency > 1, ideally we use something like rq-burst or multiprocessing
        # For a standard single-process worker:
        queue = Queue("default", connection=r)
        worker = Worker([queue], connection=r, name="baselith-worker")

        console.print(
            f"[success]Listening on queue 'default' with concurrency={concurrency}[/success]"
        )
        console.print("[dim]Press Ctrl+C to stop.[/dim]\n")

        # RQ handles its own signal catching and looping
        worker.work()

        return 0
    except KeyboardInterrupt:
        console.print("\n[yellow]Worker stopped successfully.[/yellow]")
        return 0
    except ImportError:
        print_error("RQ or Redis package not installed.")
        return 1
    except Exception as e:
        print_error("Worker failed", str(e))
        return 1


def run_queue(command: str, kwargs: dict) -> int:
    """Main entrypoint for queue commands."""
    if command == "status":
        return cmd_status()
    elif command == "worker":
        return cmd_worker(concurrency=kwargs.get("concurrency", 1))
    else:
        print_error("Unknown queue command", command)
        return 1


def register_parser(subparsers, formatter_class):
    """Register 'queue' command parser."""
    queue_parser = subparsers.add_parser(
        "queue",
        help="Manage task queues",
        description="Manage background jobs, workers, and distributed task execution using RQ and Redis.",
        formatter_class=formatter_class,
    )
    queue_subparsers = queue_parser.add_subparsers(
        dest="queue_command", title="Queue Operations"
    )
    queue_subparsers.add_parser(
        "status",
        help="List active, pending, and failed background jobs",
        formatter_class=formatter_class,
    )
    worker_parser = queue_subparsers.add_parser(
        "worker",
        help="Spawn a background task consumer (worker)",
        formatter_class=formatter_class,
    )
    worker_parser.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="Number of parallel worker processes to spawn",
    )
    return queue_parser


__all__ = ["run_queue", "register_parser"]
