"""
Verify command - Check installation and dependencies.
"""

import json as json_lib
import sys
from pathlib import Path
from rich.table import Table
from core.cli.ui import console, print_header, Timer, print_timing


def run_verify(json_output: bool = False) -> int:
    """
    Verify installation and dependencies.

    Args:
        json_output: If True, emit machine-readable JSON.

    Returns:
        Exit code (0 for success, 1 for failures)
    """
    timer = Timer()

    with timer:
        checks_passed = 0
        checks_failed = 0
        checks_warned = 0
        results_data: list[dict[str, str]] = []

        # Check Python version
        py_version = sys.version_info
        py_str = f"{py_version.major}.{py_version.minor}.{py_version.micro}"
        if py_version >= (3, 11):
            checks_passed += 1
            results_data.append(
                {
                    "status": "pass",
                    "category": "System",
                    "component": "Python Version",
                    "details": py_str,
                }
            )
        else:
            checks_failed += 1
            results_data.append(
                {
                    "status": "fail",
                    "category": "System",
                    "component": "Python Version",
                    "details": f"{py_str} (requires 3.11+)",
                }
            )

        # Check core modules
        core_modules = [
            ("core.di", "Dependency Injection"),
            ("core.config", "Configuration"),
            ("core.interfaces", "Service Protocols"),
            ("core.services.llm", "LLM Service"),
            ("core.services.vectorstore", "VectorStore Service"),
            ("core.services.chat", "Chat Service"),
            ("core.plugins", "Plugin System"),
        ]

        for module, name in core_modules:
            try:
                __import__(module)
                checks_passed += 1
                results_data.append(
                    {
                        "status": "pass",
                        "category": "Core",
                        "component": name,
                        "details": "",
                    }
                )
            except ImportError as e:
                checks_failed += 1
                results_data.append(
                    {
                        "status": "fail",
                        "category": "Core",
                        "component": name,
                        "details": str(e),
                    }
                )

        # Check dependencies
        dependencies = [
            ("fastapi", "FastAPI"),
            ("pydantic", "Pydantic"),
            ("pydantic_settings", "Pydantic Settings"),
        ]

        for module, name in dependencies:
            try:
                __import__(module)
                checks_passed += 1
                results_data.append(
                    {
                        "status": "pass",
                        "category": "Dependency",
                        "component": name,
                        "details": "",
                    }
                )
            except ImportError:
                checks_failed += 1
                results_data.append(
                    {
                        "status": "fail",
                        "category": "Dependency",
                        "component": name,
                        "details": "Not installed",
                    }
                )

        # Check optional dependencies
        optional_deps = [
            ("sentence_transformers", "Sentence Transformers (Reranker)"),
            ("qdrant_client", "Qdrant Client (Vector DB)"),
            ("langchain_text_splitters", "LangChain Text Splitters (Chunking)"),
        ]

        for module, name in optional_deps:
            try:
                __import__(module)
                results_data.append(
                    {
                        "status": "pass",
                        "category": "Optional",
                        "component": name,
                        "details": "",
                    }
                )
            except ImportError:
                checks_warned += 1
                results_data.append(
                    {
                        "status": "warn",
                        "category": "Optional",
                        "component": name,
                        "details": "Not installed",
                    }
                )

        # Check directories
        directories = [
            Path("plugins"),
            Path("data"),
            Path("documents"),
            Path("configs"),
        ]

        for dir_path in directories:
            if dir_path.exists():
                results_data.append(
                    {
                        "status": "pass",
                        "category": "Directory",
                        "component": str(dir_path),
                        "details": "Found",
                    }
                )
            else:
                checks_warned += 1
                results_data.append(
                    {
                        "status": "warn",
                        "category": "Directory",
                        "component": str(dir_path),
                        "details": "Not found",
                    }
                )

    # ── JSON output ──────────────────────────
    if json_output:
        output = {
            "passed": checks_passed,
            "warnings": checks_warned,
            "failed": checks_failed,
            "checks": results_data,
            "elapsed_seconds": round(timer.elapsed, 3),
        }
        console.print_json(json_lib.dumps(output))
        return 1 if checks_failed > 0 else 0

    # ── Rich table output ────────────────────
    print_header("🔍 Verifying Baselith-Core Installation")

    table = Table(show_header=True, header_style="bold magenta", expand=True)
    table.add_column("Status", width=8, justify="center")
    table.add_column("Category")
    table.add_column("Component")
    table.add_column("Details")

    status_icons = {
        "pass": "[green]✅[/green]",
        "fail": "[red]❌[/red]",
        "warn": "[yellow]⚠️[/yellow]",
    }

    for row in results_data:
        table.add_row(
            status_icons.get(row["status"], ""),
            row["category"],
            row["component"],
            row["details"],
        )

    console.print(table)
    console.print()

    summary_msg = f"Results: [green]{checks_passed} passed[/green]"
    if checks_warned > 0:
        summary_msg += f", [yellow]{checks_warned} warnings[/yellow]"
    if checks_failed > 0:
        summary_msg += f", [red]{checks_failed} failed[/red]"

    console.print(summary_msg)

    if checks_failed == 0:
        console.print(
            "\n[bold green]✅ Installation verified successfully![/bold green]"
        )
    else:
        console.print(f"\n[bold red]⚠️  {checks_failed} check(s) failed[/bold red]")

    print_timing(timer.elapsed)
    return 0 if checks_failed == 0 else 1


def register_parser(subparsers, formatter_class):
    """Register 'verify' command parser."""
    verify_parser = subparsers.add_parser(
        "verify",
        help="Verify environment configuration",
        description="Run a suite of checks to ensure the Python environment, core modules, and system dependencies are correctly configured.",
        formatter_class=formatter_class,
    )
    verify_parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Emit machine-readable JSON output for CI/CD pipelines",
    )


__all__ = ["run_verify", "register_parser"]
