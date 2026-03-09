#!/usr/bin/env python3
"""
Baselith-Core CLI

Scaffolding tool for creating new projects, plugins, and agents.

Usage:
    python scripts/cli.py new-project <name>
    python scripts/cli.py new-plugin <name>
    python scripts/cli.py new-agent <name>
    python scripts/cli.py list-templates
    python scripts/cli.py list-plugins
"""

import argparse
import shutil
import sys
import re
from pathlib import Path


# ============================================================================
# Configuration
# ============================================================================

PROJECT_ROOT = Path(__file__).parent.parent
TEMPLATES_DIR = PROJECT_ROOT / "templates"
PLUGINS_DIR = PROJECT_ROOT / "plugins"


# ============================================================================
# Utilities
# ============================================================================


def to_snake_case(name: str) -> str:
    """Convert name to snake_case."""
    s1 = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub("([a-z0-9])([A-Z])", r"\1_\2", s1).lower().replace("-", "_")


def to_pascal_case(name: str) -> str:
    """Convert name to PascalCase."""
    return "".join(word.capitalize() for word in name.replace("-", "_").split("_"))


def to_kebab_case(name: str) -> str:
    """Convert name to kebab-case."""
    return to_snake_case(name).replace("_", "-")


def replace_placeholders(content: str, replacements: dict) -> str:
    """Replace template placeholders."""
    for placeholder, value in replacements.items():
        content = content.replace(f"{{{{{placeholder}}}}}", value)
    return content


def copy_template(template_name: str, destination: Path, replacements: dict) -> None:
    """Copy template directory with placeholder replacement."""
    template_path = TEMPLATES_DIR / template_name

    if not template_path.exists():
        print(f"Error: Template '{template_name}' not found")
        sys.exit(1)

    if destination.exists():
        print(f"Error: Destination '{destination}' already exists")
        sys.exit(1)

    # Copy directory
    shutil.copytree(template_path, destination)

    # Replace placeholders in all files
    for file_path in destination.rglob("*"):
        if file_path.is_file():
            try:
                content = file_path.read_text()
                new_content = replace_placeholders(content, replacements)
                file_path.write_text(new_content)
            except UnicodeDecodeError:
                # Skip binary files
                pass

    print(f"✅ Created: {destination}")


# ============================================================================
# Commands
# ============================================================================


def cmd_new_project(args):
    """Create a new project from baselith-core-template template."""
    name = args.name
    destination = Path(args.output or ".") / name

    replacements = {
        "PROJECT_NAME": name,
        "PROJECT_SLUG": to_kebab_case(name),
    }

    copy_template("baselith-core-template", destination, replacements)

    print(f"""
📁 Project created: {destination}

Next steps:
  cd {destination}
  python -m venv venv
  source venv/bin/activate
  pip install -r requirements.txt
  python backend.py
""")


def cmd_new_plugin(args):
    """Create a new plugin from template."""
    name = args.name
    destination = PLUGINS_DIR / to_kebab_case(name)

    replacements = {
        "PLUGIN_NAME": name.replace("-", " ").title(),
        "PLUGIN_SLUG": to_kebab_case(name),
        "PLUGIN_CLASS_NAME": to_pascal_case(name) + "Plugin",
        "PLUGIN_DESCRIPTION": f"{name.replace('-', ' ').title()} plugin",
        "MODEL_NAME": to_pascal_case(name) + "Item",
    }

    copy_template("plugin-template", destination, replacements)

    print(f"""
🔌 Plugin created: {destination}

Next steps:
  1. Edit {destination}/plugin.py
  2. Add plugin to configs/plugins.yaml:

     {to_kebab_case(name)}:
       enabled: true

  3. Restart the server
""")


def cmd_new_agent(args):
    """Create a new agent from template."""
    name = args.name
    destination = PLUGINS_DIR / to_kebab_case(name)

    replacements = {
        "AGENT_NAME": name.replace("-", " ").title(),
        "AGENT_SLUG": to_kebab_case(name),
        "AGENT_CLASS_NAME": to_pascal_case(name) + "Agent",
        "AGENT_DESCRIPTION": f"{name.replace('-', ' ').title()} agent",
        "DOMAIN": "your domain",
        "TOPIC": "your topic",
    }

    copy_template("custom-agent-template", destination, replacements)

    print(f"""
🤖 Agent created: {destination}

Next steps:
  1. Edit {destination}/agent.py with your logic
  2. Customize {destination}/prompts/system.md
  3. Add tools in {destination}/tools.py
  4. Run tests: pytest {destination}/tests/
""")


def cmd_list_templates(args):
    """List available templates."""
    print("📋 Available templates:\n")

    for template_dir in TEMPLATES_DIR.iterdir():
        if template_dir.is_dir():
            readme = template_dir / "README.md"
            description = ""
            if readme.exists():
                lines = readme.read_text().split("\n")
                # Get first non-empty line after title
                for line in lines[2:6]:
                    if line.strip():
                        description = line.strip()
                        break

            print(f"  • {template_dir.name}")
            if description:
                print(f"    {description[:70]}...")
            print()


def cmd_list_plugins(args):
    """List installed plugins."""
    print("🔌 Installed plugins:\n")

    if not PLUGINS_DIR.exists():
        print("  (no plugins directory)")
        return

    for plugin_dir in PLUGINS_DIR.iterdir():
        if plugin_dir.is_dir():
            plugin_file = plugin_dir / "plugin.py"
            status = "✅" if plugin_file.exists() else "⚠️ (no plugin.py)"
            print(f"  {status} {plugin_dir.name}")


# ============================================================================
# Main
# ============================================================================


def main():
    parser = argparse.ArgumentParser(
        description="Baselith-Core CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s new-project my-chatbot
  %(prog)s new-plugin document-analyzer
  %(prog)s new-agent research-assistant
  %(prog)s list-templates
  %(prog)s list-plugins
""",
    )

    subparsers = parser.add_subparsers(title="commands", dest="command")

    # new-project
    p_project = subparsers.add_parser("new-project", help="Create new project")
    p_project.add_argument("name", help="Project name")
    p_project.add_argument("-o", "--output", help="Output directory (default: current)")
    p_project.set_defaults(func=cmd_new_project)

    # new-plugin
    p_plugin = subparsers.add_parser("new-plugin", help="Create new plugin")
    p_plugin.add_argument("name", help="Plugin name")
    p_plugin.set_defaults(func=cmd_new_plugin)

    # new-agent
    p_agent = subparsers.add_parser("new-agent", help="Create new agent")
    p_agent.add_argument("name", help="Agent name")
    p_agent.set_defaults(func=cmd_new_agent)

    # list-templates
    p_list_t = subparsers.add_parser("list-templates", help="List available templates")
    p_list_t.set_defaults(func=cmd_list_templates)

    # list-plugins
    p_list_p = subparsers.add_parser("list-plugins", help="List installed plugins")
    p_list_p.set_defaults(func=cmd_list_plugins)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
