"""
Lint command - Run all code quality tools.

Executes ruff, mypy, and other linters with project configuration.
"""

import subprocess  # nosec B404
import sys
from pathlib import Path
from typing import NamedTuple

from rich.table import Table
from core.cli.ui import console, print_header, print_success, Timer, print_timing


class LintResult(NamedTuple):
    """Result of a lint check."""

    tool: str
    passed: bool
    message: str


def run_ruff_check() -> LintResult:
    """Run ruff linter."""
    cmd = [
        sys.executable,
        "-m",
        "ruff",
        "check",
        ".",
        "--exclude",
        "templates,examples,frontend",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=Path.cwd())  # nosec B603
        if result.returncode == 0:
            return LintResult("Ruff (lint)", True, "No issues found")

        output = result.stdout or result.stderr
        if "No module named ruff" in output:
            return LintResult("Ruff (lint)", False, "ruff not installed in environment")

        lines = output.strip().split("\n")
        issue_count = len(
            [
                line
                for line in lines
                if "error:" in line.lower() or "warning:" in line.lower()
            ]
        )
        if issue_count == 0 and lines:
            # Fallback for different output formats
            issue_count = len(
                [
                    line
                    for line in lines
                    if line.strip() and not line.startswith("Scanning")
                ]
            )
        return LintResult("Ruff (lint)", False, f"{issue_count} issues found")
    except Exception as e:
        return LintResult("Ruff (lint)", False, f"Error: {e}")


def run_ruff_format(check_only: bool = True) -> LintResult:
    """Run ruff formatter."""
    cmd = [
        sys.executable,
        "-m",
        "ruff",
        "format",
        ".",
        "--exclude",
        "templates,examples,frontend",
    ]

    if check_only:
        cmd.append("--check")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=Path.cwd())  # nosec B603
        if result.returncode == 0:
            return LintResult("Ruff (format)", True, "All files formatted correctly")

        output = result.stdout or result.stderr
        if "No module named ruff" in output:
            return LintResult(
                "Ruff (format)", False, "ruff not installed in environment"
            )

        return LintResult("Ruff (format)", False, "Formatting issues found")
    except FileNotFoundError:
        return LintResult("Ruff (format)", False, "ruff not installed")
    except Exception as e:
        return LintResult("Ruff (format)", False, f"Error: {e}")


def run_mypy() -> LintResult:
    """Run mypy type checker."""
    cmd = [
        sys.executable,
        "-m",
        "mypy",
        "core",
        "--ignore-missing-imports",
        "--no-error-summary",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=Path.cwd())  # nosec B603
        if result.returncode == 0:
            return LintResult("MyPy (types)", True, "No type errors")

        output = result.stdout or result.stderr
        if "No module named mypy" in output:
            return LintResult(
                "MyPy (types)", False, "mypy not installed in environment"
            )

        lines = output.strip().split("\n")
        # Filter out traceback lines or non-error lines if they happen
        error_lines = [line for line in lines if "error:" in line]
        error_count = len(error_lines)

        if error_count == 0 and lines:
            # Check if it was a real failure (not just warnings)
            if any("error:" in line.lower() for line in lines):
                return LintResult("MyPy (types)", False, f"Check failed: {lines[0]}")
            return LintResult(
                "MyPy (types)", True, "No type errors found (with warnings)"
            )

        return LintResult("MyPy (types)", False, f"{error_count} type errors found")
    except Exception as e:
        return LintResult("MyPy (types)", False, f"Error: {e}")


def run_lint(
    check: bool = True,
    fix: bool = False,
    mypy: bool = True,
) -> int:
    """
    Run all linting tools.

    Args:
        check: Check mode (don't modify files)
        fix: Auto-fix issues where possible
        mypy: Include type checking

    Returns:
        Exit code (0 if all pass)
    """
    print_header("🔍 Baselith-Core Linter")

    timer = Timer()
    with timer:
        results: list[LintResult] = []

        # Run formatters
        if fix:
            with console.status("[bold blue]🔧 Running in fix mode..."):
                # Ruff fix
                ruff_fix = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "ruff",
                        "check",
                        ".",
                        "--fix",
                        "--exclude",
                        "templates,examples,frontend",
                    ],
                    capture_output=True,
                    text=True,
                    cwd=Path.cwd(),
                )  # nosec B603

                # Ruff format
                subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "ruff",
                        "format",
                        ".",
                        "--exclude",
                        "templates,examples,frontend",
                    ],
                    capture_output=True,
                    text=True,
                    cwd=Path.cwd(),
                )  # nosec B603

                # Check if ruff was missing
                output = (ruff_fix.stdout or "") + (ruff_fix.stderr or "")
                if "No module named ruff" in output:
                    console.print(
                        "\n[yellow]⚠️  Warning: 'ruff' is not installed in the current Python environment.[/yellow]"
                    )
                    console.print(
                        "[dim]Skipping auto-fix. Install it with: pip install ruff[/dim]\n"
                    )
                else:
                    print_success("Auto-fix complete")
            console.print()

        with console.status("[bold cyan]Running linting checks..."):
            results.append(run_ruff_check())
            results.append(run_ruff_format(check_only=check))

            if mypy:
                results.append(run_mypy())

        # Display results
        console.print()
        passed = 0
        failed = 0

        table = Table(show_header=True, header_style="bold magenta", expand=True)
        table.add_column("Status", width=8, justify="center")
        table.add_column("Tool")
        table.add_column("Message")

        for result in results:
            if result.passed:
                status = "[green]✅ PASS[/green]"
                passed += 1
            else:
                status = "[red]❌ FAIL[/red]"
                failed += 1

            table.add_row(status, result.tool, result.message)

        console.print(table)
        console.print()

        summary_msg = f"Results: [green]{passed} passed[/green]"
        if failed > 0:
            summary_msg += f", [red]{failed} failed[/red]"
        console.print(summary_msg)

        if failed > 0:
            all_missing = all(
                "not installed" in r.message.lower() for r in results if not r.passed
            )
            if all_missing:
                console.print(
                    "\n[yellow]💡 Tip: Install missing tools with 'pip install ruff mypy'[/yellow]"
                )
            else:
                console.print(
                    "\n[yellow]💡 Tip: Run 'baselith lint --fix' to auto-fix issues[/yellow]"
                )
            print_timing(timer.elapsed)
            return 1

        console.print("\n[bold green]✅ All checks passed![/bold green]")
        print_timing(timer.elapsed)
        return 0


def register_parser(subparsers, formatter_class):
    """Register 'lint' command parser."""
    lint_parser = subparsers.add_parser(
        "lint",
        help="Run code linters",
        description="Run Ruff and MyPy to ensure code quality, formatting compliance, and type safety.",
        formatter_class=formatter_class,
    )
    lint_parser.add_argument(
        "--fix",
        action="store_true",
        help="Automatically resolve formatting and linting violations",
    )
    lint_parser.add_argument(
        "--no-mypy",
        action="store_true",
        help="Bypass static type checking with MyPy",
    )
    return lint_parser


__all__ = ["run_lint", "register_parser"]
