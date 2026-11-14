"""``baselith update policy get|set``, ``update runs`` and ``update resolve``.

``policy set`` writes the auto-apply policy straight into the run store: a
shell on the host is trusted with it, as with ``plugin-updater request``, so
there is no second approval here (a console change is a proposal another
administrator approves). The change is journaled in the store with the actor
``cli:<os user>`` and logged as ``AUDIT | UPDATE_POLICY | set``. ``runs`` lists
the runs of every subject; ``resolve`` frees a ``rollback_failed`` run of any
subject, or closes a stuck ``finalizing`` core run by what the API serves (the
``plugin-updater resolve`` semantics). None of them loads plugin
code. Exit codes: 0 ok, 1 refused or I/O failure, 2 invalid input.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import time

from pydantic import ValidationError

from baselith.core.cli.commands._update_apply import operator
from baselith.core.config.plugin_update_apply import get_update_apply_config
from baselith.core.plugin_updates.apply.models import ApplyRun
from baselith.core.plugin_updates.apply.store import RunStore
from baselith.core.plugin_updates.policy import (
    MaintenanceWindow,
    PolicyProposal,
    UpdatePolicy,
)
from baselith.core.plugin_updates.preflight import normalize_subject

logger = logging.getLogger(__name__)

_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class _BadInput(ValueError):
    """Command-line input the policy cannot be built from (exit 2)."""


def add_policy_parsers(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
    formatter_class: type[argparse.HelpFormatter],
) -> None:
    """Register ``policy get|set``, ``runs`` and ``resolve`` under ``update``."""
    policy = sub.add_parser(
        "policy",
        help="Show or set the auto-apply policy (patch releases in a window)",
        formatter_class=formatter_class,
    )
    ops = policy.add_subparsers(dest="policy_command", title="Operations")
    ops.add_parser(
        "get",
        help="Print the live policy and a pending proposal",
        formatter_class=formatter_class,
    )
    put = ops.add_parser(
        "set",
        help="Replace the live policy (the host shell is trusted)",
        formatter_class=formatter_class,
    )
    for flag in ("--core", "--plugins"):
        put.add_argument(flag, choices=["off", "patch"], default="off")
    put.add_argument(
        "--allow",
        action="append",
        default=[],
        metavar="NAME",
        help="A plugin the plugins channel may update (repeat; none: every plugin)",
    )
    put.add_argument("--days", help="Comma-separated: mon,tue,wed,thu,fri,sat,sun")
    put.add_argument("--start", help="Window start, HH:MM wall-clock in --tz")
    put.add_argument("--end", help="Window end, HH:MM (before --start: spans midnight)")
    put.add_argument("--tz", help="IANA time zone of the window (e.g. Europe/Rome)")
    runs = sub.add_parser(
        "runs",
        help="List update runs of the core and the plugins, newest first",
        formatter_class=formatter_class,
    )
    runs.add_argument("--subject", help="core, or a plugin name")
    runs.add_argument("--limit", type=int, default=20)
    resolve = sub.add_parser(
        "resolve",
        help="Free a rollback_failed run once the operator put it right, or "
        "close a stuck finalizing one",
        formatter_class=formatter_class,
    )
    resolve.add_argument("run_id")


def _store() -> RunStore:
    return RunStore(get_update_apply_config().state_dir)


def _err(message: str) -> None:
    print(f"baselith update: {message}", file=sys.stderr)


def _window(kwargs: dict[str, object]) -> MaintenanceWindow | None:
    parts = {k: kwargs.get(k) for k in ("days", "start", "end", "tz")}
    given = [k for k, v in parts.items() if v]
    if not given:
        return None
    if len(given) != len(parts):
        raise _BadInput("--days, --start, --end and --tz go together")
    days: set[int] = set()
    for name in str(parts["days"]).split(","):
        key = name.strip().lower()[:3]
        if key not in _DAYS:
            raise _BadInput(f"unknown day {name.strip()!r} (mon..sun)")
        days.add(_DAYS.index(key))
    try:
        start = time.fromisoformat(str(parts["start"]))
        end = time.fromisoformat(str(parts["end"]))
    except ValueError as exc:
        raise _BadInput(f"window times are HH:MM ({exc})") from exc
    return MaintenanceWindow(
        days=frozenset(days), start=start, end=end, tz=str(parts["tz"])
    )


def _describe_window(window: MaintenanceWindow | None) -> str:
    if window is None:
        return "none"
    days = ",".join(_DAYS[d] for d in sorted(window.days))
    return f"{days} {window.start:%H:%M}-{window.end:%H:%M} {window.tz}"


def _summary(policy: UpdatePolicy) -> dict[str, str]:
    allow = ",".join(sorted(policy.plugin_allowlist)) or "all"
    return {
        "core": policy.core,
        "plugins": policy.plugins,
        "allow": allow,
        "window": _describe_window(policy.window),
    }


def _policy_set(kwargs: dict[str, object]) -> int:
    try:
        allow = kwargs.get("allow")
        policy = UpdatePolicy.model_validate(
            {
                "core": kwargs.get("core") or "off",
                "plugins": kwargs.get("plugins") or "off",
                "plugin_allowlist": list(allow) if isinstance(allow, list) else [],
                "window": _window(kwargs),
            }
        )
    except _BadInput as exc:
        _err(str(exc))
        return 2
    except ValidationError as exc:
        _err("; ".join(e["msg"].removeprefix("Value error, ") for e in exc.errors()))
        return 2
    store = _store()
    actor = operator()
    try:
        before = store.get_policy()
        store.set_policy(policy, actor=actor)
    except OSError as exc:
        _err(f"cannot write the policy: {type(exc).__name__}")
        return 1
    logger.warning(
        "AUDIT | UPDATE_POLICY | set core=%s plugins=%s by=%s",
        policy.core,
        policy.plugins,
        actor,
    )
    old, new = _summary(before), _summary(policy)
    changed = [f"{k}: {old[k]} -> {new[k]}" for k in new if old[k] != new[k]]
    print("\n".join(changed) if changed else "policy unchanged")
    return 0


def _proposal_json(p: PolicyProposal | None) -> object:
    if p is None:
        return None
    return {**p.model_dump(mode="json"), "digest": p.digest}


def _policy_get(fmt: str) -> int:
    store = _store()
    policy, proposal = store.get_policy(), store.pending_proposal()
    if fmt == "json":
        payload = {
            "policy": policy.model_dump(mode="json"),
            "proposal": _proposal_json(proposal),
        }
        print(json.dumps(payload, indent=2))
        return 0
    if not policy.enabled:
        print("policy: off (nothing installs itself)")
    else:
        s = _summary(policy)
        print(f"policy: core={s['core']} plugins={s['plugins']} allow={s['allow']}")
        print(f"window: {s['window']}")
    if proposal is None:
        print("proposal: none")
    else:
        s = _summary(proposal.policy)
        print(
            f"proposal: by {proposal.proposed_by}, expires "
            f"{proposal.expires_at.isoformat()}: core={s['core']} "
            f"plugins={s['plugins']} window={s['window']}"
        )
    return 0


def _row(run: ApplyRun) -> dict[str, object]:
    return {
        "id": run.id,
        "subject": run.subject_key,
        "kind": run.kind.value,
        "state": run.state.value,
        "requested_by": run.requested_by,
        "from_version": run.from_version,
        "to_version": run.to_version,
        "requested_at": run.requested_at.isoformat(),
    }


def _runs(kwargs: dict[str, object], fmt: str) -> int:
    wanted = str(kwargs.get("subject") or "").strip()
    key = normalize_subject(wanted) if wanted else None
    limit = int(str(kwargs.get("limit") or 20))
    if limit < 1:
        _err("--limit must be at least 1")
        return 2
    found = [
        r for r in reversed(_store().runs()) if key is None or r.subject_key == key
    ][:limit]
    if fmt == "json":
        print(json.dumps([_row(r) for r in found], indent=2))
        return 0
    if not found:
        print("no runs")
    for r in found:
        print(
            f"{r.id} {r.subject_key} {r.kind.value} {r.state.value} "
            f"{r.from_version or '-'} -> {r.to_version or '-'} by={r.requested_by}"
        )
    return 0


def run_policy_command(command: str, kwargs: dict[str, object], fmt: str) -> int:
    """Dispatch ``policy``, ``runs`` or ``resolve``; the exit code."""
    if command == "runs":
        return _runs(kwargs, fmt)
    if command == "resolve":
        from baselith.core.cli.commands.plugin_updater import run_plugin_updater

        return run_plugin_updater("resolve", kwargs)
    sub = kwargs.get("policy_command") or "get"
    if sub == "set":
        return _policy_set(kwargs)
    return _policy_get(fmt)


__all__ = ["add_policy_parsers", "run_policy_command"]
