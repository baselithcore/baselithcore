"""
Documentation utility commands.
"""

import json
from pathlib import Path

from core.cli.ui import console, print_header, print_error


def cmd_generate() -> int:
    """Generate OpenAPI specifications as static files."""
    print_header("📚 OpenAPI Generation", "Export API Specifications")

    with console.status("[bold blue]Scanning endpoints...", spinner="dots"):
        try:
            from core.api.factory import create_app

            app = create_app()
            schema = app.openapi()

            # Count endpoints to be informative
            endpoints_count = sum(
                len(methods) for path, methods in schema.get("paths", {}).items()
            )
            console.print(f"[success]✅ Found {endpoints_count} endpoints[/success]")

            # Define output directory
            docs_dir = Path.cwd() / "mkdocs-site" / "docs" / "api" / "specs"
            docs_dir.mkdir(parents=True, exist_ok=True)

            # Export JSON
            json_out = docs_dir / "openapi.json"
            with open(json_out, "w") as f:
                json.dump(schema, f, indent=2)
            console.print(
                f"[success]✅ Generated: {json_out.relative_to(Path.cwd())}[/success]"
            )

            # Try to export YAML if pyyaml is installed
            try:
                import yaml

                yaml_out = docs_dir / "openapi.yaml"
                with open(yaml_out, "w") as f:
                    yaml.dump(schema, f, sort_keys=False)
                console.print(
                    f"[success]✅ Generated: {yaml_out.relative_to(Path.cwd())}[/success]"
                )
            except ImportError:
                console.print("[dim]PyYAML not installed, skipping YAML export.[/dim]")

            # The actual conversion from OpenAPI to Postman 2.1 is complex.
            # We'll just generate the file so the user knows they could use `openapi-to-postmanv2`.

            console.print(
                "\n[dim]To import into Postman, use the generated openapi.json file.[/dim]\n"
            )

            return 0
        except ImportError as e:
            print_error("Failed importing core FastAPI factory", str(e))
            return 1
        except Exception as e:
            print_error("Failed to generate documentation", str(e))
            return 1


def run_docs(command: str) -> int:
    """Main entrypoint for docs commands."""
    if command == "generate":
        return cmd_generate()
    else:
        print_error("Unknown docs command", command)
        return 1


def register_parser(subparsers, formatter_class):
    """Register 'docs' command parser."""
    docs_parser = subparsers.add_parser(
        "docs",
        help="Generate documentation",
        description="Automate the creation of API specifications, static sites, and technical documentation.",
        formatter_class=formatter_class,
    )
    docs_subparsers = docs_parser.add_subparsers(
        dest="docs_command", title="Documentation Actions"
    )
    docs_subparsers.add_parser(
        "generate",
        help="Export OpenAPI static files and JSON schemas",
        formatter_class=formatter_class,
    )
    return docs_parser


__all__ = ["run_docs", "register_parser"]
