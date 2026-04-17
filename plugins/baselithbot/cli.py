"""``baselith baselithbot ...`` CLI extension."""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any


def _cmd_run(args: argparse.Namespace) -> int:
    """Execute a one-shot Baselithbot task from the CLI."""
    from plugins.baselithbot import BaselithbotAgent, BaselithbotTask

    async def _go() -> dict[str, Any]:
        agent = BaselithbotAgent(
            config={
                "headless": not args.headed,
                "max_steps": args.max_steps,
            }
        )
        await agent.startup()
        try:
            result = await agent.execute(
                BaselithbotTask(
                    goal=args.goal,
                    start_url=args.start_url,
                    max_steps=args.max_steps,
                )
            )
            return result.model_dump()
        finally:
            await agent.shutdown()

    payload = asyncio.run(_go())
    print(json.dumps(payload, indent=2, default=str))
    return 0 if payload.get("success") else 1


def _cmd_status(_args: argparse.Namespace) -> int:
    """Print local manifest status for the Baselithbot plugin."""
    del _args
    from pathlib import Path

    import yaml  # type: ignore[import-untyped]

    manifest_path = Path("plugins/baselithbot/manifest.yaml")
    if not manifest_path.is_file():
        print("baselithbot: manifest.yaml not found")
        return 1
    data = yaml.safe_load(manifest_path.read_text())
    print(
        f"baselithbot: {data.get('version', 'unknown')} "
        f"({data.get('readiness', 'unknown')})"
    )
    return 0


def _cmd_onboard(_args: argparse.Namespace) -> int:
    """Walk the operator through the minimum viable Baselithbot config."""
    del _args
    print("Baselithbot onboarding wizard")
    print("=" * 60)
    print("Press ENTER to accept defaults shown in [brackets].\n")

    headless = input("Run browser headless? [Y/n]: ").strip().lower() or "y"
    enable_cu = (
        input("Enable Computer Use (mouse/kbd/screen)? [y/N]: ").strip().lower() or "n"
    )
    enable_shell = input("Allow shell tool? [y/N]: ").strip().lower() or "n"
    fs_root = input("Filesystem root (empty = disabled): ").strip()
    audit = input("Audit log path (empty = stderr only): ").strip()

    config = {
        "enabled": True,
        "headless": headless.startswith("y"),
        "stealth": {"enabled": True},
        "computer_use": {
            "enabled": enable_cu.startswith("y"),
            "allow_shell": enable_shell.startswith("y"),
            "allow_filesystem": bool(fs_root),
            "filesystem_root": fs_root or None,
            "audit_log_path": audit or None,
        },
    }

    import json

    print("\nProposed configs/plugins.yaml block:\n")
    print("baselithbot:")
    for line in json.dumps(config, indent=2).splitlines():
        print(f"  {line}")
    print("\nCopy the block above into configs/plugins.yaml under 'baselithbot:'")
    return 0


def _dispatch(args: argparse.Namespace) -> int:
    """Top-level handler invoked by ``COMMAND_HANDLERS_MAP['baselithbot']``."""
    func = getattr(args, "func", None)
    if func is None:
        print("usage: baselith baselithbot {run,status} ...")
        return 1
    return int(func(args))


def register_parser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
    formatter_class: type[argparse.HelpFormatter],
) -> argparse.ArgumentParser:
    """Register the ``baselithbot`` command tree on the main CLI."""
    parser = subparsers.add_parser(
        "baselithbot",
        help="Run or inspect the Baselithbot autonomous browser agent.",
        formatter_class=formatter_class,
    )
    sub = parser.add_subparsers(dest="baselithbot_cmd", required=True)

    run = sub.add_parser("run", help="Execute a Baselithbot task.")
    run.add_argument("goal", help="Natural-language goal.")
    run.add_argument("--start-url", default=None, help="Optional landing URL.")
    run.add_argument("--max-steps", type=int, default=20)
    run.add_argument(
        "--headed",
        action="store_true",
        help="Show the browser window (default: headless).",
    )
    run.set_defaults(func=_cmd_run)

    status = sub.add_parser("status", help="Show plugin registration status.")
    status.set_defaults(func=_cmd_status)

    onboard = sub.add_parser("onboard", help="Interactive onboarding wizard.")
    onboard.set_defaults(func=_cmd_onboard)

    try:
        from core.cli.__main__ import COMMAND_HANDLERS_MAP

        COMMAND_HANDLERS_MAP["baselithbot"] = _dispatch
    except Exception:
        pass

    return parser


__all__ = ["register_parser"]
