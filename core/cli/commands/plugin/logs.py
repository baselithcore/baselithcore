"""
Plugin log viewing commands.

Reads structured log files from `logs/` and filters entries
by plugin name, log level, and line count.
"""

import json
import re
from pathlib import Path
from typing import Optional


from core.cli.ui import console, print_error, print_warning


_LEVEL_COLORS = {
    "DEBUG": "dim",
    "INFO": "cyan",
    "WARNING": "yellow",
    "ERROR": "bold red",
    "CRITICAL": "bold red on white",
}

_VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def _find_log_files() -> list[Path]:
    """Discover log files in the logs/ directory."""
    logs_dir = Path("logs")
    if not logs_dir.exists():
        return []
    return sorted(logs_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)


def _parse_log_line(line: str) -> Optional[dict]:
    """
    Attempt to parse a structured JSON log line.

    Falls back to regex for standard text log format:
    ``YYYY-MM-DD HH:MM:SS [LEVEL] module: message``
    """
    stripped = line.strip()
    if not stripped:
        return None

    # Try JSON first
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
            return {
                "timestamp": data.get("timestamp", data.get("time", "")),
                "level": data.get("level", data.get("levelname", "INFO")).upper(),
                "module": data.get("module", data.get("name", data.get("logger", ""))),
                "message": data.get("message", data.get("msg", "")),
                "raw": stripped,
            }
        except json.JSONDecodeError:
            pass

    # Text format: "2025-12-25 10:00:00 [INFO] plugins.example: message"
    pattern = r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:[.,]\d+)?)\s+\[?(\w+)\]?\s+([^:]+):\s*(.*)$"
    match = re.match(pattern, stripped)
    if match:
        return {
            "timestamp": match.group(1),
            "level": match.group(2).upper(),
            "module": match.group(3).strip(),
            "message": match.group(4).strip(),
            "raw": stripped,
        }

    return {
        "timestamp": "",
        "level": "INFO",
        "module": "",
        "message": stripped,
        "raw": stripped,
    }


def plugin_logs(
    plugin_name: str,
    lines: int = 50,
    level: Optional[str] = None,
    json_output: bool = False,
) -> int:
    """
    Display log entries filtered by plugin name.

    Searches all log files in ``logs/`` for entries mentioning
    the plugin name, optionally filtering by log level.

    Args:
        plugin_name: Plugin name to filter by.
        lines: Maximum number of lines to display.
        level: Minimum log level filter (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        json_output: Output as JSON array.

    Returns:
        Exit code.
    """
    if level and level.upper() not in _VALID_LEVELS:
        print_error(
            f"Invalid log level '{level}'. Valid levels: {', '.join(sorted(_VALID_LEVELS))}"
        )
        return 1

    level_upper = level.upper() if level else None
    level_order = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    min_level_idx = level_order.index(level_upper) if level_upper else 0

    log_files = _find_log_files()
    if not log_files:
        print_warning("No log files found in logs/ directory.")
        return 0

    matching_entries: list[dict] = []

    for log_file in log_files:
        try:
            with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                for raw_line in f:
                    parsed = _parse_log_line(raw_line)
                    if parsed is None:
                        continue

                    # Filter by plugin name
                    name_lower = plugin_name.lower().replace("-", "_")
                    searchable = f"{parsed['module']} {parsed['message']}".lower()
                    if (
                        name_lower not in searchable
                        and plugin_name.lower() not in searchable
                    ):
                        continue

                    # Filter by level
                    if level_upper:
                        entry_level_idx = (
                            level_order.index(parsed["level"])
                            if parsed["level"] in level_order
                            else 0
                        )
                        if entry_level_idx < min_level_idx:
                            continue

                    matching_entries.append(parsed)
        except Exception:
            continue

    # Take the last N entries
    matching_entries = matching_entries[-lines:]

    if not matching_entries:
        console.print(f"[dim]No log entries found for plugin '{plugin_name}'.[/dim]")
        return 0

    if json_output:
        print(
            json.dumps(
                [{k: v for k, v in e.items() if k != "raw"} for e in matching_entries],
                indent=2,
            )
        )
        return 0

    console.print()
    console.print(
        f"[bold blue]Logs for plugin:[/bold blue] [bold]{plugin_name}[/bold]  "
        f"[dim]({len(matching_entries)} entries)[/dim]"
    )
    console.print()

    for entry in matching_entries:
        lvl = entry["level"]
        color = _LEVEL_COLORS.get(lvl, "white")
        ts = f"[dim]{entry['timestamp']}[/dim] " if entry["timestamp"] else ""
        mod = f"[cyan]{entry['module']}[/cyan]: " if entry["module"] else ""
        console.print(f"  {ts}[{color}]{lvl:8s}[/{color}] {mod}{entry['message']}")

    console.print()
    return 0
