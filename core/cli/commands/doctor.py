"""Doctor command - advanced system diagnostics."""

import json as json_lib

from rich.table import Table

from core.cli.commands import doctor_checks as checks
from core.cli.commands import doctor_plugin_checks as plugin_checks
from core.cli.commands.doctor_checks import CheckResult, apply_fixes
from core.cli.ui import Timer, console, print_header, print_timing

check_port = checks.check_port
check_python_runtime = checks.check_python_runtime
check_env_file = checks.check_env_file
check_data_dirs = checks.check_data_dirs
check_docker = checks.check_docker
check_core_dependencies = checks.check_core_dependencies
check_telemetry = checks.check_telemetry
check_migrations_mode = checks.check_migrations_mode
check_plugins = plugin_checks.check_plugins
check_plugin_dependencies = plugin_checks.check_plugin_dependencies
check_plugin_frontends = plugin_checks.check_plugin_frontends


def _with_compatible_port_patch(fn):
    original = checks.check_port
    checks.check_port = check_port
    try:
        return fn()
    finally:
        checks.check_port = original


def check_redis() -> CheckResult:
    """Check Redis connectivity."""
    return _with_compatible_port_patch(checks.check_redis)


def check_qdrant() -> CheckResult:
    """Check Qdrant vector store connectivity."""
    return _with_compatible_port_patch(checks.check_qdrant)


def check_graph_db() -> CheckResult:
    """Check graph database connectivity."""
    return _with_compatible_port_patch(checks.check_graph_db)


def check_postgres() -> CheckResult:
    """Check PostgreSQL database connectivity."""
    return _with_compatible_port_patch(checks.check_postgres)


def check_llm_provider() -> CheckResult:
    """Check LLM provider availability."""
    return _with_compatible_port_patch(checks.check_llm_provider)


def _severity_for(check: CheckResult) -> str:
    if check.passed:
        return "pass"
    return check.severity


def run_checks(include_plugins: bool = True) -> list[CheckResult]:
    """Run all doctor checks in display order."""
    core_checks = [
        check_python_runtime(),
        check_env_file(),
        check_data_dirs(),
        check_docker(),
        check_core_dependencies(),
        check_llm_provider(),
        check_redis(),
        check_qdrant(),
        check_postgres(),
        check_graph_db(),
        check_telemetry(),
        check_migrations_mode(),
    ]
    if not include_plugins:
        return core_checks
    return [
        *core_checks,
        check_plugins(),
        check_plugin_dependencies(),
        check_plugin_frontends(),
    ]


def run_doctor(
    json_output: bool = False, fix: bool = False, include_plugins: bool = True
) -> int:
    """
    Run comprehensive system diagnostics.

    Args:
        json_output: If True, emit machine-readable JSON instead of Rich tables.
        fix: If True, apply safe local fixes before running diagnostics.
        include_plugins: If True, include plugin readiness checks.

    Returns:
        Exit code (0 if all critical checks pass).
    """
    timer = Timer()
    fixes = apply_fixes() if fix else []

    with timer:
        if not json_output:
            print_header("🩺 Baselith-Core Doctor", "System Diagnostics")

        with console.status("[bold blue]Running diagnostics...", spinner="dots"):
            checks = run_checks(include_plugins=include_plugins)

    passed = 0
    failed = 0
    warnings = 0

    if json_output:
        results = []
        for check in checks:
            severity = _severity_for(check)
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
            "fixes": fixes,
            "elapsed_seconds": round(timer.elapsed, 3),
        }
        console.print_json(json_lib.dumps(output))
        return 1 if failed > 0 else 0

    table = Table(show_header=True, header_style="bold magenta", expand=True)
    table.add_column("Status", style="dim", width=8, justify="center")
    table.add_column("Component")
    table.add_column("Message")
    table.add_column("Details/Resolution", style="dim")

    for check in checks:
        if check.passed:
            status_text = "[green]✅ PASS[/green]"
            passed += 1
        elif check.severity == "warn":
            status_text = "[yellow]⚠️ WARN[/yellow]"
            warnings += 1
        else:
            status_text = "[red]❌ FAIL[/red]"
            failed += 1

        table.add_row(status_text, check.name, check.message, check.details)

    console.print(table)
    if fixes:
        console.print()
        console.print("[bold green]Applied fixes:[/bold green]")
        for item in fixes:
            console.print(f"  - {item}")
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
        description=(
            "Verify project structure, infrastructure connectivity, plugin "
            "readiness, and local runtime configuration."
        ),
        formatter_class=formatter_class,
    )
    doctor_parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Emit machine-readable JSON output for CI/CD pipelines",
    )
    doctor_parser.add_argument(
        "--fix",
        action="store_true",
        default=False,
        help="Apply safe local fixes such as creating .env and data directories",
    )
    doctor_parser.add_argument(
        "--core-only",
        action="store_true",
        help="Skip plugin checks and validate only the core runtime",
    )


__all__ = ["register_parser", "run_doctor"]
