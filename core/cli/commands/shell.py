"""
Shell command - Interactive REPL.
"""

from typing import Any

from core.cli.ui import console, print_header


def run_shell() -> int:
    """Start an interactive Python shell with the Baselith-Core context pre-loaded."""
    print_header("💻 Baselith-Core Shell", "Interactive Python REPL")

    # Pre-load common components
    context: dict[str, Any] = {}
    with console.status("[bold blue]Loading core context...", spinner="dots"):
        try:
            from core.config import get_core_config

            context["settings"] = get_core_config()
        except Exception:
            pass

        try:
            from core.services.llm.service import LLMService, get_llm_service

            context["LLMService"] = LLMService
            context["get_llm_service"] = get_llm_service
        except Exception:
            pass

        try:
            from core.services.vectorstore.interfaces import VectorStoreProtocol

            context["VectorStoreProtocol"] = VectorStoreProtocol
        except Exception:
            pass

    console.print("[dim]Pre-loaded into context:[/dim]")
    for k in context:
        console.print(f"  - [cyan]{k}[/cyan]")
    console.print()

    # Try IPython first, fallback to code.interact
    try:
        import IPython
        from traitlets.config import Config

        c = Config()
        c.TerminalInteractiveShell.banner1 = ""
        c.TerminalInteractiveShell.banner2 = ""
        IPython.start_ipython(argv=[], config=c, user_ns=context)  # type: ignore
    except ImportError:
        import code

        console.print(
            "[yellow]IPython not found. Falling back to built-in REPL.[/yellow]"
        )
        code.interact(banner="", local=context)

    return 0


def register_parser(subparsers, formatter_class):
    """Register 'shell' command parser."""
    subparsers.add_parser(
        "shell",
        help="Start interactive shell",
        description="Start an IPython or standard Python shell with the Baselith-Core environment, configuration, and core services pre-loaded.",
        formatter_class=formatter_class,
    )


__all__ = ["register_parser", "run_shell"]
