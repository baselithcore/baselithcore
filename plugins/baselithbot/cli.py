"""``baselith baselithbot ...`` CLI extension."""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import shutil
import subprocess  # nosec B404 - argv list, shell=False
from pathlib import Path
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


def _cmd_onboard(args: argparse.Namespace) -> int:
    """Walk the operator through the minimum viable Baselithbot config."""
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

    if getattr(args, "write", False):
        rc = _write_onboarding_block(config, getattr(args, "config_path", None))
        if rc != 0:
            return rc
    else:
        print("\nProposed configs/plugins.yaml block:\n")
        print("baselithbot:")
        for line in json.dumps(config, indent=2).splitlines():
            print(f"  {line}")
        print(
            "\nCopy the block above into configs/plugins.yaml under 'baselithbot:'"
            " or re-run with --write to apply the change in place."
        )

    if getattr(args, "install_daemon", False):
        return _install_daemon(dry_run=getattr(args, "dry_run", False))
    return 0


def _install_daemon(*, dry_run: bool) -> int:
    """Install the platform-native service unit for Baselithbot.

    macOS → ``~/Library/LaunchAgents`` via ``launchctl``; Linux → ``systemctl``
    user scope (fallback to printing the unit path when unprivileged).
    """
    system = platform.system()
    plugin_dir = Path(__file__).resolve().parent
    if system == "Darwin":
        src = plugin_dir / "deploy" / "launchd" / "com.baselith.baselithbot.plist"
        dst = Path.home() / "Library" / "LaunchAgents" / src.name
        return _install_launchd(src, dst, dry_run=dry_run)
    if system == "Linux":
        src = plugin_dir / "deploy" / "systemd" / "baselithbot.service"
        dst = Path.home() / ".config" / "systemd" / "user" / src.name
        return _install_systemd(src, dst, dry_run=dry_run)
    print(f"Unsupported platform for daemon install: {system}")
    return 1


def _install_launchd(src: Path, dst: Path, *, dry_run: bool) -> int:
    if not src.is_file():
        print(f"plist template missing: {src}")
        return 1
    print(f"plist: {src} → {dst}")
    if dry_run:
        return 0
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    launchctl = shutil.which("launchctl")
    if launchctl is None:
        print("launchctl not found; plist copied but not loaded")
        return 0
    result = subprocess.run(  # nosec B603
        [launchctl, "load", "-w", str(dst)],
        shell=False,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(f"launchctl load failed: {result.stderr.strip()}")
        return 1
    print(f"Loaded launchd agent {dst.stem}")
    return 0


def _install_systemd(src: Path, dst: Path, *, dry_run: bool) -> int:
    if not src.is_file():
        print(f"service template missing: {src}")
        return 1
    print(f"unit: {src} → {dst}")
    if dry_run:
        return 0
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    systemctl = shutil.which("systemctl")
    if systemctl is None:
        print("systemctl not found; unit copied but not enabled")
        return 0
    for action in ("daemon-reload", "enable", "start"):
        result = subprocess.run(  # nosec B603
            [systemctl, "--user", action, dst.stem],
            shell=False,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            print(f"systemctl --user {action} failed: {result.stderr.strip()}")
            return 1
    print(f"Enabled systemd user unit {dst.stem}")
    return 0


def _write_onboarding_block(config: dict[str, Any], path_arg: str | None) -> int:
    """Merge the onboarding block into ``configs/plugins.yaml`` in place."""
    import yaml  # type: ignore[import-untyped]

    target = Path(path_arg) if path_arg else Path("configs/plugins.yaml")
    if not target.is_file():
        print(f"plugins.yaml not found at {target}; aborting")
        return 1
    data: dict[str, Any] = yaml.safe_load(target.read_text()) or {}
    existing = data.get("baselithbot") or {}
    merged = {**existing, **config}
    data["baselithbot"] = merged
    target.write_text(yaml.safe_dump(data, sort_keys=False))
    print(f"Updated {target} (baselithbot block merged)")
    return 0


def _load_plugins_yaml(path_arg: str | None) -> tuple[Path, dict[str, Any]]:
    import yaml  # type: ignore[import-untyped]

    target = Path(path_arg) if path_arg else Path("configs/plugins.yaml")
    if not target.is_file():
        raise FileNotFoundError(f"plugins.yaml not found at {target}")
    data: dict[str, Any] = yaml.safe_load(target.read_text()) or {}
    return target, data


def _save_plugins_yaml(target: Path, data: dict[str, Any]) -> None:
    import yaml  # type: ignore[import-untyped]

    target.write_text(yaml.safe_dump(data, sort_keys=False))


def _cmd_pairing_approve(args: argparse.Namespace) -> int:
    """Append ``<sender>`` to ``baselithbot.dm_policy.<channel>.allowed_senders``."""
    try:
        target, data = _load_plugins_yaml(getattr(args, "config_path", None))
    except FileNotFoundError as exc:
        print(str(exc))
        return 1
    section = data.setdefault("baselithbot", {})
    policies = section.setdefault("dm_policy", {})
    channel_policy = policies.setdefault(args.channel, {})
    allowed: list[str] = list(channel_policy.get("allowed_senders") or [])
    if args.sender in allowed:
        print(f"{args.sender} already approved on {args.channel}")
        return 0
    allowed.append(args.sender)
    channel_policy["allowed_senders"] = allowed
    _save_plugins_yaml(target, data)
    print(f"Approved {args.sender} on {args.channel} ({target})")
    return 0


def _cmd_pairing_list(args: argparse.Namespace) -> int:
    """Print configured dm_policy allowlists."""
    try:
        _, data = _load_plugins_yaml(getattr(args, "config_path", None))
    except FileNotFoundError as exc:
        print(str(exc))
        return 1
    policies = (data.get("baselithbot") or {}).get("dm_policy") or {}
    if not policies:
        print("No dm_policy channels configured.")
        return 0
    print(json.dumps(policies, indent=2, sort_keys=True))
    return 0


def _cmd_pairing_token(_args: argparse.Namespace) -> int:
    """Issue a one-shot pairing token (in-process; dev aid)."""
    del _args
    from .nodes.pairing import NodePairing

    token = NodePairing().issue_token()
    print(token)
    return 0


def _cmd_gateway(args: argparse.Namespace) -> int:
    """Launch the FastAPI backend on ``--port`` (baselith gateway)."""
    import uvicorn  # type: ignore[import-untyped]

    log_level = "debug" if getattr(args, "verbose", False) else "info"
    if getattr(args, "install_daemon", False):
        return _install_daemon(dry_run=False)
    uvicorn.run(
        "backend:app",
        host=args.host,
        port=args.port,
        log_level=log_level,
    )
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
    onboard.add_argument(
        "--write",
        action="store_true",
        help="Write the resulting block into configs/plugins.yaml in place.",
    )
    onboard.add_argument(
        "--config-path",
        default=None,
        help="Override the path to plugins.yaml (default: configs/plugins.yaml).",
    )
    onboard.add_argument(
        "--install-daemon",
        action="store_true",
        help="Install launchd (macOS) / systemd user (Linux) service unit.",
    )
    onboard.add_argument(
        "--dry-run",
        action="store_true",
        help="Print daemon install target paths without writing or loading.",
    )
    onboard.set_defaults(func=_cmd_onboard)

    pairing = sub.add_parser(
        "pairing", help="Manage DM policy allowlists and pairing tokens."
    )
    pairing_sub = pairing.add_subparsers(dest="pairing_cmd", required=True)

    approve = pairing_sub.add_parser(
        "approve",
        help="Approve <sender> on <channel> (persisted to configs/plugins.yaml).",
    )
    approve.add_argument("channel", help="Channel identifier (e.g. slack, telegram).")
    approve.add_argument("sender", help="Sender ID / handle to allow.")
    approve.add_argument(
        "--config-path",
        default=None,
        help="Override plugins.yaml path (default: configs/plugins.yaml).",
    )
    approve.set_defaults(func=_cmd_pairing_approve)

    listing = pairing_sub.add_parser("list", help="Show configured dm_policy map.")
    listing.add_argument("--config-path", default=None)
    listing.set_defaults(func=_cmd_pairing_list)

    token = pairing_sub.add_parser(
        "token", help="Print a one-shot pairing token (dev aid; in-process)."
    )
    token.set_defaults(func=_cmd_pairing_token)

    gateway = sub.add_parser(
        "gateway", help="Launch the Baselith gateway (FastAPI backend)."
    )
    gateway.add_argument("--host", default="127.0.0.1")
    gateway.add_argument("--port", type=int, default=8000)
    gateway.add_argument("--verbose", action="store_true", help="Debug log level.")
    gateway.add_argument(
        "--install-daemon",
        action="store_true",
        help="Install daemon unit instead of running the server.",
    )
    gateway.set_defaults(func=_cmd_gateway)

    try:
        from core.cli.__main__ import COMMAND_HANDLERS_MAP

        COMMAND_HANDLERS_MAP["baselithbot"] = _dispatch
    except Exception:
        pass

    return parser


__all__ = ["register_parser"]
