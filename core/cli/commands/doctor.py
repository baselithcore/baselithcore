"""
Doctor command - Advanced system diagnostics.

Performs comprehensive health checks on all system components using core configuration.
"""

import json as json_lib
import socket
from pathlib import Path
from typing import NamedTuple, Tuple

from rich.table import Table
from core.cli.ui import console, print_header, Timer, print_timing


class CheckResult(NamedTuple):
    """Result of a health check."""

    name: str
    passed: bool
    message: str
    details: str = ""


def check_port(host: str, port: int, timeout: float = 2.0) -> bool:
    """Check if a port is open and accepting connections."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except Exception:
        return False


def parse_url(url: str, default_port: int) -> Tuple[str, int]:
    """Parse a URL to extract host and port."""
    try:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        host = parsed.hostname or "localhost"
        port = parsed.port or default_port
        return host, port
    except Exception:
        return "localhost", default_port


def check_redis() -> CheckResult:
    """Check Redis connectivity (Cache)."""
    try:
        from core.config import get_storage_config

        config = get_storage_config()
        redis_url = config.cache_redis_url
        host, port = parse_url(redis_url, 6379)

        if check_port(host, port):
            return CheckResult("Redis (Cache)", True, f"Connected ({host}:{port})")

        return CheckResult(
            "Redis (Cache)",
            False,
            f"Cannot connect ({host}:{port})",
            "Run: docker-compose up -d redis",
        )
    except ImportError:
        return CheckResult(
            "Redis", False, "ImportError", "Could not import core.config"
        )
    except Exception as e:
        return CheckResult("Redis", False, f"Error: {e}")


def check_qdrant() -> CheckResult:
    """Check Qdrant vector store connectivity."""
    try:
        from core.config import get_vectorstore_config

        config = get_vectorstore_config()

        if config.provider != "qdrant":
            return CheckResult(
                "Vector Store",
                True,
                f"Provider: {config.provider} (Skipping Qdrant check)",
            )

        host = config.host
        port = config.port

        if check_port(host, port):
            return CheckResult("Qdrant", True, f"Connected ({host}:{port})")

        return CheckResult(
            "Qdrant",
            False,
            f"Cannot connect ({host}:{port})",
            "Run: docker-compose up -d qdrant",
        )
    except Exception as e:
        return CheckResult("Qdrant", False, f"Error: {e}")


def check_graph_db() -> CheckResult:
    """Check Graph Database connectivity."""
    try:
        from core.config import get_storage_config

        config = get_storage_config()

        if not config.graph_db_enabled:
            return CheckResult("GraphDB", True, "Disabled (Skipping check)")

        graph_url = config.graph_db_url
        host, port = parse_url(
            graph_url, 6379
        )  # Defaults to Redis port if using RedisGraph

        if check_port(host, port):
            return CheckResult("GraphDB", True, f"Connected ({host}:{port})")

        return CheckResult(
            "GraphDB",
            False,
            f"Cannot connect ({host}:{port})",
            "Run: docker-compose up -d neo4j/redis",
        )
    except Exception as e:
        return CheckResult("GraphDB", False, f"Error: {e}")


def check_postgres() -> CheckResult:
    """Check PostgreSQL database connectivity."""
    try:
        from core.config import get_storage_config

        config = get_storage_config()

        if not config.postgres_enabled:
            return CheckResult("PostgreSQL", True, "Disabled (Skipping check)")

        pg_url = config.conninfo
        if not pg_url:
            return CheckResult("PostgreSQL", False, "Missing database configuration")

        host, port = parse_url(
            pg_url.replace("postgresql+asyncpg://", "http://").replace(
                "postgresql://", "http://"
            ),
            5432,
        )

        if check_port(host, port):
            return CheckResult("PostgreSQL", True, f"Connected ({host}:{port})")

        return CheckResult(
            "PostgreSQL",
            False,
            f"Cannot connect ({host}:{port})",
            "Run: docker-compose up -d postgres",
        )
    except Exception as e:
        return CheckResult("PostgreSQL", False, f"Error: {e}")


def check_env_file() -> CheckResult:
    """Check if .env file exists."""
    env_path = Path.cwd() / "configs" / ".env"

    # Also check root .env as fallback/standard
    root_env = Path.cwd() / ".env"

    if env_path.exists():
        return CheckResult("Environment", True, f"Found config at {env_path}")
    elif root_env.exists():
        return CheckResult("Environment", True, f"Found config at {root_env}")

    return CheckResult(
        "Environment",
        False,
        ".env file not found",
        "Run: cp configs/.env.base .env",
    )


def check_plugins() -> CheckResult:
    """Check if plugins directory exists and has plugins."""
    plugins_path = Path.cwd() / "plugins"

    if not plugins_path.exists():
        return CheckResult(
            "Plugins",
            False,
            "plugins/ directory not found",
        )

    # Count valid plugins
    plugins = [
        p for p in plugins_path.iterdir() if p.is_dir() and (p / "plugin.py").exists()
    ]

    if not plugins:
        return CheckResult("Plugins", True, "No plugins installed (optional)")

    return CheckResult("Plugins", True, f"{len(plugins)} plugin(s) found")


def check_llm_provider() -> CheckResult:
    """Check LLM provider availability."""
    try:
        from core.config import get_llm_config

        config = get_llm_config()
        provider = config.provider

        if provider == "ollama":
            ollama_url = config.api_base or "http://localhost:11434"
            host, port = parse_url(ollama_url, 11434)

            if check_port(host, port):
                return CheckResult(
                    "LLM Provider", True, f"Ollama connected ({host}:{port})"
                )

            return CheckResult(
                "LLM Provider",
                False,
                f"Ollama not reachable ({host}:{port})",
                "Run: ollama serve",
            )

        elif provider in (
            "openai",
            "huggingface",
        ):
            if config.api_key:
                return CheckResult(
                    "LLM Provider", True, f"{provider.upper()} API key configured"
                )
            return CheckResult(
                "LLM Provider",
                False,
                f"{provider.upper()} API key missing",
                "Set LLM_API_KEY in .env",
            )

        return CheckResult("LLM Provider", True, f"Provider: {provider}")
    except Exception as e:
        return CheckResult("LLM Provider", False, f"Error: {e}")


def run_doctor(json_output: bool = False) -> int:
    """
    Run comprehensive system diagnostics.

    Args:
        json_output: If True, emit machine-readable JSON instead of Rich tables.

    Returns:
        Exit code (0 if all critical checks pass)
    """
    timer = Timer()

    with timer:
        if not json_output:
            print_header("🩺 Baselith-Core Doctor", "System Diagnostics")

        # Run all checks
        with console.status("[bold blue]Running diagnostics...", spinner="dots"):
            checks = [
                check_env_file(),
                check_llm_provider(),
                check_redis(),
                check_qdrant(),
                check_postgres(),
                check_graph_db(),
                check_plugins(),
            ]

    passed = 0
    failed = 0
    warnings = 0

    # ── JSON output ──────────────────────────
    if json_output:
        results = []
        for check in checks:
            severity = "pass"
            if not check.passed:
                severity = "warn" if check.name in ("GraphDB", "Plugins") else "fail"
            results.append(
                {
                    "name": check.name,
                    "passed": check.passed,
                    "severity": severity,
                    "message": check.message,
                    "details": check.details,
                }
            )
            if check.passed:
                passed += 1
            elif severity == "warn":
                warnings += 1
            else:
                failed += 1

        output = {
            "passed": passed,
            "warnings": warnings,
            "failed": failed,
            "checks": results,
            "elapsed_seconds": round(timer.elapsed, 3),
        }
        console.print_json(json_lib.dumps(output))
        return 1 if failed > 0 else 0

    # ── Rich table output ────────────────────
    table = Table(show_header=True, header_style="bold magenta", expand=True)
    table.add_column("Status", style="dim", width=8, justify="center")
    table.add_column("Component")
    table.add_column("Message")
    table.add_column("Details/Resolution", style="dim")

    for check in checks:
        if check.passed:
            status_text = "[green]✅ PASS[/green]"
            passed += 1
        else:
            if check.name in ("GraphDB", "Plugins"):
                status_text = "[yellow]⚠️ WARN[/yellow]"
                warnings += 1
            else:
                status_text = "[red]❌ FAIL[/red]"
                failed += 1

        table.add_row(status_text, check.name, check.message, check.details)

    console.print(table)
    console.print()

    summary_text = f"Results: [green]{passed} passed[/green]"
    if warnings > 0:
        summary_text += f", [yellow]{warnings} warnings[/yellow]"
    if failed > 0:
        summary_text += f", [red]{failed} failed[/red]"

    console.print(summary_text)

    if failed > 0:
        console.print(
            "\n[bold red]⚠️  Some critical checks failed. Fix them before running the server.[/bold red]"
        )
        print_timing(timer.elapsed)
        return 1

    console.print("\n[bold green]✅ System ready! Run: baselith run[/bold green]")
    print_timing(timer.elapsed)
    return 0


def register_parser(subparsers, formatter_class):
    """Register 'doctor' command parser."""
    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Run system diagnostics",
        description="Verify project structure, connectivity to infrastructure (Redis, DB, LLM), and framework compliance.",
        formatter_class=formatter_class,
    )
    doctor_parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Emit machine-readable JSON output for CI/CD pipelines",
    )


__all__ = ["run_doctor", "register_parser"]
