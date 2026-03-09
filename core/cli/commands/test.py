"""
Test command - Run test suite with optimal configuration.

Wraps pytest with sensible defaults for the baselith-core.
"""

import subprocess  # nosec B404
import sys
from pathlib import Path
from typing import Optional

from rich.panel import Panel
from core.cli.ui import console, print_header, print_error, Timer, print_timing
import json


def run_test(
    path: Optional[str] = None,
    coverage: bool = True,
    verbose: bool = False,
    markers: Optional[str] = None,
    parallel: bool = False,
    fail_fast: bool = False,
    json_output: bool = False,
) -> int:
    """
    Run the test suite with pytest.

    Args:
        path: Optional path to test file or directory
        coverage: Enable coverage reporting
        verbose: Verbose output
        markers: Pytest markers to filter (e.g., 'unit', 'integration')
        parallel: Run tests in parallel (requires pytest-xdist)
        fail_fast: Stop on first failure

    Returns:
        Exit code from pytest
    """
    if not json_output:
        print_header("🧪 Baselith-Core Test Runner")

    # Build pytest command
    cmd = [sys.executable, "-m", "pytest"]

    # Add path or default to tests/
    if path:
        cmd.append(path)
    else:
        tests_path = Path.cwd() / "tests"
        if tests_path.exists():
            cmd.append("tests/")
        else:
            if json_output:
                print(
                    json.dumps(
                        {"status": "error", "message": "No tests/ directory found"}
                    )
                )
            else:
                print_error("No tests/ directory found")
            return 1

    # Coverage options
    if coverage:
        cmd.extend(["--cov=core", "--cov=app", "--cov-report=term-missing"])

    # Verbose mode
    if verbose:
        cmd.append("-v")
    else:
        cmd.append("-q")

    # Markers
    if markers:
        cmd.extend(["-m", markers])

    # Parallel execution
    if parallel:
        cmd.extend(["-n", "auto"])

    # Fail fast
    if fail_fast:
        cmd.append("-x")

    # Ignore templates and examples
    cmd.extend(["--ignore=templates", "--ignore=examples"])

    if json_output:
        print(json.dumps({"status": "starting", "command": " ".join(cmd)}))
    else:
        # Display command
        console.print(
            Panel(
                f"[bold cyan]Command:[/bold cyan] {' '.join(cmd)}",
                title="[bold blue]Test Execution[/bold blue]",
                border_style="blue",
            )
        )
        console.print()

    # Run pytest
    timer = Timer()
    try:
        with timer:
            result = subprocess.run(cmd, cwd=Path.cwd())  # nosec B603

        if json_output:
            print(
                json.dumps(
                    {
                        "status": "completed",
                        "exit_code": result.returncode,
                        "duration": timer.elapsed,
                    }
                )
            )
        else:
            print_timing(timer.elapsed)
        return result.returncode
    except FileNotFoundError:
        if json_output:
            print(
                json.dumps(
                    {
                        "status": "error",
                        "message": "pytest not found. Install with: pip install pytest pytest-cov",
                    }
                )
            )
        else:
            print_error("pytest not found. Install with: pip install pytest pytest-cov")
        return 1


def run_test_unit() -> int:
    """Run only unit tests."""
    return run_test(path="tests/unit/", coverage=True, verbose=False)


def run_test_integration() -> int:
    """Run only integration tests."""
    return run_test(path="tests/integration/", coverage=False, verbose=True)


def register_parser(subparsers, formatter_class):
    """Register 'test' command parser."""
    test_parser = subparsers.add_parser(
        "test",
        help="Run test suite",
        description="Run pytest with integrated coverage reporting and optimized parallel execution.",
        formatter_class=formatter_class,
    )
    test_parser.add_argument(
        "path",
        nargs="?",
        default=None,
        help="Specific test file or directory to execute (default: tests/)",
    )
    test_parser.add_argument(
        "--no-cov",
        action="store_true",
        help="Omit code coverage analysis for faster execution",
    )
    test_parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Provide detailed output for each test case",
    )
    test_parser.add_argument(
        "-m",
        "--markers",
        default=None,
        help="Filter tests by pytest markers (e.g., 'unit', 'integration')",
    )
    test_parser.add_argument(
        "-x",
        "--fail-fast",
        action="store_true",
        help="Terminate immediately upon the first test failure",
    )
    test_parser.add_argument(
        "--parallel",
        action="store_true",
        help="Harness multiple CPU cores for parallel test execution",
    )
    return test_parser


__all__ = ["run_test", "run_test_unit", "run_test_integration", "register_parser"]
