"""
Central UI utility for Baselith-Core CLI using Rich.

Provides a consistent, premium visual experience across all CLI commands
with a curated theme, gradient text, timing, and reusable output helpers.
"""

import time
from typing import Any, Optional, Sequence

from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from rich.theme import Theme
from rich import box

# ──────────────────────────────────────────
# Theme
# ──────────────────────────────────────────

custom_theme = Theme(
    {
        "info": "cyan",
        "warning": "yellow",
        "error": "bold red",
        "success": "bold green",
        "step": "blue",
        "highlight": "magenta bold",
        "accent": "bold bright_cyan",
        "muted": "dim white",
        "brand": "bold cyan",
        "prompt": "bold magenta",
        "key": "bold cyan",
        "value": "white",
    }
)

console = Console(theme=custom_theme)
err_console = Console(theme=custom_theme, stderr=True)

# ──────────────────────────────────────────
# Color palette for gradient text
# ──────────────────────────────────────────

_GRADIENT_COLORS = [
    "#1ec2ad",  # Cyan (#2fe7cc slightly darker/denser)
    "#26a6ce",  # Midpoint Cyan-Blue
    "#2aa0db",  # Sky Blue (#6dd2ff slightly darker/denser)
    "#8eb1ac",  # Transition
    "#d1a364",  # Peach/Sand (#ffc07a toned down, strictly not strong orange)
    "#8eb1ac",
    "#26a6ce",
]


# ──────────────────────────────────────────
# Message helpers
# ──────────────────────────────────────────


def print_success(message: str) -> None:
    """Print a success message."""
    console.print(f"[success]✅ {message}[/success]")


def print_error(message: str, details: Optional[str] = None) -> None:
    """Print an error message with optional details."""
    err_console.print(f"[error]❌ Error:[/error] {message}")
    if details:
        err_console.print(f"   [dim]{details}[/dim]")


def print_warning(message: str) -> None:
    """Print a warning message."""
    console.print(f"[warning]⚠️  Warning:[/warning] {message}")


def print_info(message: str) -> None:
    """Print an informational message."""
    console.print(f"[info]ℹ️  {message}[/info]")


def print_step(message: str) -> None:
    """Print a step/progress message."""
    console.print(f"[step]🚀 {message}[/step]")


# ──────────────────────────────────────────
# Layout helpers
# ──────────────────────────────────────────


def print_header(title: str, subtitle: str = "") -> None:
    """Print a formatted header panel."""
    content = Text(title, justify="center", style="bold white")
    if subtitle:
        content.append(f"\n{subtitle}", style="dim")

    console.print()
    console.print(Panel(content, border_style="blue", expand=False))
    console.print()


def print_panel(content: Any, title: Optional[str] = None, style: str = "blue") -> None:
    """Print content enclosed in a panel."""
    console.print(Panel(content, title=title, border_style=style, expand=False))


def print_rule(title: str = "", style: str = "dim") -> None:
    """Print a horizontal rule/separator."""
    console.print(Rule(title=title, style=style))


def print_key_value(
    pairs: dict[str, str],
    title: Optional[str] = None,
    border_style: str = "blue",
) -> None:
    """Print a clean key-value panel (for dashboards/status)."""
    table = Table(box=None, show_header=False, padding=(0, 2), expand=False)
    table.add_column("Key", style="key", width=20)
    table.add_column("Value", style="value")

    for k, v in pairs.items():
        table.add_row(k, v)

    if title:
        console.print(
            Panel(
                table,
                title=f"[bold]{title}[/bold]",
                border_style=border_style,
                expand=False,
            )
        )
    else:
        console.print(table)


def print_results_table(
    rows: Sequence[dict[str, str]],
    columns: Sequence[dict[str, Any]],
    title: Optional[str] = None,
) -> None:
    """
    Quick table builder from list of dicts.

    Args:
        rows: List of row dicts (keys must match column "key").
        columns: List of column definitions, each a dict with "key", "header",
                 and optional Rich Table.add_column kwargs (style, width, justify).
        title: Optional table title.
    """
    table = Table(
        title=title,
        title_style="bold blue",
        show_header=True,
        header_style="bold magenta",
        expand=True,
        box=box.ROUNDED,
    )

    for col in columns:
        col_kwargs = {k: v for k, v in col.items() if k not in ("key", "header")}
        table.add_column(col["header"], **col_kwargs)

    for row in rows:
        table.add_row(*(row.get(col["key"], "") for col in columns))

    console.print(table)


# ──────────────────────────────────────────
# Gradient / branding helpers
# ──────────────────────────────────────────


def create_gradient_text(text: str, bold: bool = True) -> Text:
    """
    Render a string with a horizontal color gradient.

    Cycles through the brand gradient palette character-by-character.
    """
    rich_text = Text()
    visible_index = 0
    for char in text:
        if char.strip():
            color = _GRADIENT_COLORS[visible_index % len(_GRADIENT_COLORS)]
            style = f"bold {color}" if bold else color
            rich_text.append(char, style=style)
            visible_index += 1
        else:
            rich_text.append(char)
    return rich_text


# ──────────────────────────────────────────
# Timing helpers
# ──────────────────────────────────────────


class Timer:
    """Context manager for timing CLI operations."""

    def __init__(self) -> None:
        self._start: float = 0.0
        self.elapsed: float = 0.0

    def __enter__(self) -> "Timer":
        self._start = time.perf_counter()
        return self

    def __exit__(self, *args: Any) -> None:
        self.elapsed = time.perf_counter() - self._start


def print_timing(elapsed: float) -> None:
    """Print a formatted execution duration."""
    if elapsed < 1.0:
        formatted = f"{elapsed * 1000:.0f}ms"
    elif elapsed < 60.0:
        formatted = f"{elapsed:.2f}s"
    else:
        minutes = int(elapsed // 60)
        seconds = elapsed % 60
        formatted = f"{minutes}m {seconds:.1f}s"
    console.print(f"\n[muted]⏱  Completed in {formatted}[/muted]")
