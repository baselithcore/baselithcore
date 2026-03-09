"""
Config command - Show and validate configuration.
"""

from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from core.cli.ui import console, print_header


def show_config() -> int:
    """
    Show current configuration.

    Returns:
        Exit code (0 for success)
    """
    print_header("📋 Current Configuration")

    layout = Layout()
    layout.split_column(Layout(name="top"), Layout(name="bottom"))
    layout["top"].split_row(Layout(name="core"), Layout(name="llm"))
    layout["bottom"].split_row(Layout(name="chat"), Layout(name="vectorstore"))

    try:
        from core.config import get_core_config

        core_cfg = get_core_config()
        t_core = Table(show_header=False, expand=True)
        t_core.add_column("Key", style="dim")
        t_core.add_column("Value", style="cyan bold")
        t_core.add_row("Log Level", core_cfg.log_level)
        t_core.add_row("Debug", str(core_cfg.debug))
        t_core.add_row("Plugin Dir", str(core_cfg.plugin_dir))
        t_core.add_row("Data Dir", str(core_cfg.data_dir))

        layout["core"].update(Panel(t_core, title="Core Settings", border_style="blue"))
    except ImportError:
        layout["core"].update(
            Panel("[red]Core config not available[/red]", title="Core Settings")
        )

    # LLM config
    try:
        from core.config import get_llm_config

        llm_cfg = get_llm_config()
        t_llm = Table(show_header=False, expand=True)
        t_llm.add_column("Key", style="dim")
        t_llm.add_column("Value", style="cyan bold")
        t_llm.add_row("Provider", llm_cfg.provider)
        t_llm.add_row("Model", llm_cfg.model)
        t_llm.add_row(
            "Cache Enabled",
            str(
                getattr(
                    llm_cfg, "cache_enabled", getattr(llm_cfg, "enable_cache", "N/A")
                )
            ),
        )

        layout["llm"].update(Panel(t_llm, title="LLM Settings", border_style="magenta"))
    except ImportError:
        layout["llm"].update(
            Panel("[red]LLM config not available[/red]", title="LLM Settings")
        )

    # Chat config
    try:
        from core.config import get_chat_config

        chat_cfg = get_chat_config()
        t_chat = Table(show_header=False, expand=True)
        t_chat.add_column("Key", style="dim")
        t_chat.add_column("Value", style="cyan bold")
        t_chat.add_row("Streaming", str(chat_cfg.streaming_enabled))
        t_chat.add_row("Initial Search K", str(chat_cfg.initial_search_k))
        t_chat.add_row("Final Top K", str(chat_cfg.final_top_k))

        layout["chat"].update(
            Panel(t_chat, title="Chat Settings", border_style="green")
        )
    except ImportError:
        layout["chat"].update(
            Panel("[red]Chat config not available[/red]", title="Chat Settings")
        )

    # VectorStore config
    try:
        from core.config import get_vectorstore_config

        vs_cfg = get_vectorstore_config()
        t_vs = Table(show_header=False, expand=True)
        t_vs.add_column("Key", style="dim")
        t_vs.add_column("Value", style="cyan bold")
        t_vs.add_row("Provider", vs_cfg.provider)

        host = getattr(vs_cfg, "qdrant_host", getattr(vs_cfg, "host", "N/A"))
        port = getattr(vs_cfg, "qdrant_port", getattr(vs_cfg, "port", "N/A"))
        t_vs.add_row("Host:Port", f"{host}:{port}")
        t_vs.add_row(
            "Collection",
            str(
                getattr(
                    vs_cfg, "qdrant_collection", getattr(vs_cfg, "collection", "N/A")
                )
            ),
        )

        layout["vectorstore"].update(
            Panel(t_vs, title="VectorStore Settings", border_style="yellow")
        )
    except ImportError:
        layout["vectorstore"].update(
            Panel(
                "[red]VectorStore config not available[/red]",
                title="VectorStore Settings",
            )
        )

    console.print(layout)

    return 0


def validate_config() -> int:
    """
    Validate configuration.

    Returns:
        Exit code (0 for success, 1 for failures)
    """
    print_header("🔍 Validating Configuration")

    errors = []
    warnings = []

    table = Table(show_header=True, header_style="bold magenta", expand=True)
    table.add_column("Status", width=8, justify="center")
    table.add_column("Component")
    table.add_column("Message/Details")

    with console.status("[bold blue]Checking configurations..."):
        # Check core config
        try:
            from core.config import get_core_config

            _core = get_core_config()
            table.add_row("[green]✅[/green]", "Core", "Config valid")
        except Exception as e:
            errors.append(f"Core config error: {e}")
            table.add_row("[red]❌[/red]", "Core", str(e))

        # Check LLM config
        try:
            from core.config import get_llm_config

            _llm = get_llm_config()
            if _llm.provider not in ["ollama", "openai"]:
                warnings.append(f"Unknown LLM provider: {_llm.provider}")
                table.add_row(
                    "[yellow]⚠️[/yellow]", "LLM", f"Unknown provider: {_llm.provider}"
                )
            else:
                table.add_row("[green]✅[/green]", "LLM", "Config valid")
        except Exception as e:
            errors.append(f"LLM config error: {e}")
            table.add_row("[red]❌[/red]", "LLM", str(e))

        # Check Chat config
        try:
            from core.config import get_chat_config

            _chat = get_chat_config()
            table.add_row("[green]✅[/green]", "Chat", "Config valid")
        except Exception as e:
            errors.append(f"Chat config error: {e}")
            table.add_row("[red]❌[/red]", "Chat", str(e))

        # Check VectorStore config
        try:
            from core.config import get_vectorstore_config

            _vs = get_vectorstore_config()
            table.add_row("[green]✅[/green]", "VectorStore", "Config valid")
        except Exception as e:
            errors.append(f"VectorStore config error: {e}")
            table.add_row("[red]❌[/red]", "VectorStore", str(e))

    console.print(table)
    console.print()

    # Report errors & final conclusion
    if errors:
        console.print(
            "[bold red]❌ Configuration validation failed with errors.[/bold red]"
        )
        return 1

    if warnings:
        console.print(
            "[bold yellow]⚠️  Configuration passed with warnings.[/bold yellow]"
        )
        return 0

    console.print("[bold green]✅ All configurations valid![/bold green]")
    return 0


def register_parser(subparsers, formatter_class):
    """Register 'config' command parser."""
    config_parser = subparsers.add_parser(
        "config",
        help="Manage configuration",
        description="Manage environment variables, LLM settings, and infrastructure connections.",
        formatter_class=formatter_class,
    )
    config_subparsers = config_parser.add_subparsers(
        dest="config_command", title="Config Operations"
    )
    config_subparsers.add_parser(
        "show",
        help="Print redacted summary of current configuration",
        formatter_class=formatter_class,
    )
    config_subparsers.add_parser(
        "validate",
        help="Check .env integrity and required fields",
        formatter_class=formatter_class,
    )
    return config_parser


__all__ = ["show_config", "validate_config", "register_parser"]
