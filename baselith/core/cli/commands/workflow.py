"""
Workflow commands: validate, run, export, list and draft definitions, and work
with the signed inbound trigger.

Definitions are YAML or JSON files; see the Workflow Engine page of the docs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

from baselith.core.cli.ui import (
    console,
    print_error,
    print_results_table,
    print_success,
    print_warning,
)


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, default=str))


def _load(path: str) -> Any:
    from baselith.core.workflows.serialization import (
        DefinitionError,
        load_definition_file,
    )

    try:
        return load_definition_file(path), None
    except (DefinitionError, OSError) as exc:
        return None, str(exc)


def _parse_input(options: dict[str, Any]) -> Any:
    """``--input`` / ``--input-file`` as JSON when it parses, else as text."""
    raw: str | None
    if options.get("input_file"):
        raw = Path(options["input_file"]).read_text(encoding="utf-8")
    else:
        raw = options.get("input")
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def _validate(options: dict[str, Any], json_output: bool) -> int:
    results = []
    for path in options["files"]:
        workflow, load_error = _load(path)
        errors = [load_error] if load_error else workflow.validate()
        results.append({"file": path, "valid": not errors, "errors": errors})
    if json_output:
        _emit({"results": results})
    else:
        for item in results:
            if item["valid"]:
                print_success(f"{item['file']}: valid")
            else:
                print_error(f"{item['file']}: invalid")
                for error in item["errors"]:
                    console.print(f"  - {error}")
    return 0 if all(r["valid"] for r in results) else 1


def _run(options: dict[str, Any], json_output: bool) -> int:
    from baselith.core.workflows.runtime import default_executor_factory

    workflow, load_error = _load(options["file"])
    errors = [load_error] if load_error else workflow.validate()
    if errors:
        print_error(f"{options['file']}: invalid", "; ".join(errors))
        return 1
    try:
        payload = _parse_input(options)
    except OSError as exc:
        print_error("Could not read the input", str(exc))
        return 1

    started = time.perf_counter()
    try:
        result = asyncio.run(default_executor_factory()().execute(workflow, payload))
    except Exception as exc:
        # A HUMAN gate raises here: a one-shot CLI run has no checkpoint store
        # to pause into, so it fails closed instead of waving the gate through.
        print_error("Run did not complete", f"{type(exc).__name__}: {exc}")
        return 1
    elapsed_ms = (time.perf_counter() - started) * 1000
    ok = result.status.value == "completed"
    if json_output:
        _emit(
            {
                "status": result.status.value,
                "output": result.output,
                "error": result.error,
                "duration_ms": round(elapsed_ms, 1),
            }
        )
    elif ok:
        print(
            result.output
            if isinstance(result.output, str)
            else json.dumps(result.output, indent=2, default=str)
        )
    else:
        print_error("Run failed", result.error)
    return 0 if ok else 1


def _export(options: dict[str, Any], json_output: bool) -> int:
    from baselith.core.workflows.serialization import DefinitionError, dump_yaml

    workflow, load_error = _load(options["file"])
    if load_error:
        print_error(f"{options['file']}: unreadable", load_error)
        return 1
    try:
        text = dump_yaml(workflow) if options["to"] == "yaml" else workflow.to_json()
    except (DefinitionError, TypeError) as exc:
        print_error("Cannot export", str(exc))
        return 1
    if options.get("output"):
        Path(options["output"]).write_text(text, encoding="utf-8")
        print_success(f"Wrote {options['output']}")
    else:
        sys.stdout.write(text if text.endswith("\n") else text + "\n")
    return 0


def _catalog(options: dict[str, Any]) -> Any:
    from baselith.core.config.workflows import get_workflows_config
    from baselith.core.workflows.catalog import WorkflowCatalog

    directory = options.get("dir") or get_workflows_config().directory
    return WorkflowCatalog(directory) if directory else None


def _list(options: dict[str, Any], json_output: bool) -> int:
    catalog = _catalog(options)
    if catalog is None:
        print_error("No directory", "Pass --dir or set WORKFLOWS_DIR.")
        return 1
    report = catalog.load_directory()
    rows = [
        {
            "id": w.id,
            "name": w.name,
            "nodes": str(len(w.nodes)),
            "schedule": w.schedule or "-",
            "trigger": "yes" if w.webhook_trigger else "-",
        }
        for w in catalog.list()
    ]
    if json_output:
        _emit({"workflows": rows, "failed": report.failed})
    else:
        if rows:
            print_results_table(
                rows,
                [
                    {"key": "id", "header": "ID", "style": "cyan"},
                    {"key": "name", "header": "Name"},
                    {"key": "nodes", "header": "Nodes", "justify": "right"},
                    {"key": "schedule", "header": "Schedule"},
                    {"key": "trigger", "header": "Trigger"},
                ],
                title=f"Workflows in {catalog.directory}",
            )
        else:
            print_warning(f"No workflow definitions in {catalog.directory}")
        for name, reason in report.failed.items():
            print_error(f"{name}: rejected", reason)
    return 1 if report.failed else 0


def _master() -> str | None:
    from baselith.core.config.workflows import get_workflows_config

    secret = get_workflows_config().trigger_secret
    value = secret.get_secret_value() if secret else ""
    return value or None


def _trigger_secret(options: dict[str, Any], json_output: bool) -> int:
    from baselith.core.workflows.triggers import derive_workflow_secret

    master = _master()
    if master is None:
        print_error("WORKFLOWS_TRIGGER_SECRET is not set")
        return 1
    key = derive_workflow_secret(master, options["workflow_id"])
    if json_output:
        _emit({"workflow_id": options["workflow_id"], "secret": key})
    else:
        print(
            key
        )  # bare, so it can be captured: KEY=$(baselith workflow trigger-secret ID)
    return 0


def _trigger_sign(options: dict[str, Any], json_output: bool) -> int:
    from baselith.core.webhooks.signing import SIGNATURE_HEADER, build_signature_header
    from baselith.core.workflows.triggers import derive_workflow_secret

    master = _master()
    if master is None:
        print_error("WORKFLOWS_TRIGGER_SECRET is not set")
        return 1
    try:
        body = (
            Path(options["body_file"]).read_bytes()
            if options.get("body_file")
            else sys.stdin.buffer.read()
        )
    except OSError as exc:
        print_error("Could not read the body", str(exc))
        return 1
    header = build_signature_header(
        derive_workflow_secret(master, options["workflow_id"]), body
    )
    if json_output:
        _emit({"header": SIGNATURE_HEADER, "value": header})
    else:
        print(f"{SIGNATURE_HEADER}: {header}")
    return 0


def _generate(options: dict[str, Any], json_output: bool) -> int:
    from baselith.core.workflows.generator import GenerationError, generate_workflow

    existing = None
    if options.get("revise"):
        existing, load_error = _load(options["revise"])
        if load_error:
            print_error(f"{options['revise']}: unreadable", load_error)
            return 1
    try:
        result = asyncio.run(
            generate_workflow(
                options["description"],
                existing=existing,
                max_attempts=options.get("attempts") or 3,
            )
        )
    except GenerationError as exc:
        print_error("No valid workflow after the allowed attempts")
        for error in exc.errors[:10]:
            console.print(f"  - {error}")
        return 1
    except Exception as exc:
        print_error("Generation failed", f"{type(exc).__name__}: {exc}")
        return 1
    if not json_output:
        # stderr, so `baselith workflow generate ... > w.yaml` stays valid YAML.
        for warning in result.warnings:
            print(f"warning: {warning}", file=sys.stderr)
    if options.get("output"):
        Path(options["output"]).write_text(result.yaml, encoding="utf-8")
        if not json_output:
            print_success(f"Wrote {options['output']} ({result.attempts} attempt(s))")
    elif not json_output:
        sys.stdout.write(result.yaml)
    if json_output:
        _emit(
            {
                "attempts": result.attempts,
                "warnings": result.warnings,
                "workflow": result.definition.to_dict(),
            }
        )
    return 0


_COMMANDS = {
    "generate": _generate,
    "validate": _validate,
    "run": _run,
    "export": _export,
    "list": _list,
    "trigger-secret": _trigger_secret,
    "trigger-sign": _trigger_sign,
}


def run_workflow(
    command: str, options: dict[str, Any], *, json_output: bool = False
) -> int:
    """Dispatch a ``baselith workflow`` subcommand; returns the exit code."""
    handler = _COMMANDS.get(command)
    if handler is None:
        print_error("Unknown workflow command", command)
        return 1
    return handler(options, json_output)


def register_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    formatter_class: type[argparse.HelpFormatter],
) -> argparse.ArgumentParser:
    """Register the 'workflow' command parser."""
    parser = subparsers.add_parser(
        "workflow",
        help="Validate, run and manage workflow definitions",
        description="Work with workflow definitions (YAML or JSON): check them, "
        "run them once, convert them, list a catalog directory, and handle the "
        "signing key of the inbound trigger.",
        formatter_class=formatter_class,
    )
    sub = parser.add_subparsers(dest="workflow_command", title="Workflow Operations")

    validate = sub.add_parser(
        "validate",
        help="Check one or more definition files",
        formatter_class=formatter_class,
    )
    validate.add_argument("files", nargs="+", metavar="FILE")

    run = sub.add_parser(
        "run",
        help="Run a definition once with the configured services",
        formatter_class=formatter_class,
    )
    run.add_argument("file", metavar="FILE")
    run.add_argument("--input", help="Initial input (JSON if it parses, else text)")
    run.add_argument("--input-file", help="Read the initial input from a file")

    export = sub.add_parser(
        "export",
        help="Convert a definition between YAML and JSON",
        formatter_class=formatter_class,
    )
    export.add_argument("file", metavar="FILE")
    export.add_argument("--to", choices=["yaml", "json"], default="yaml")
    export.add_argument("-o", "--output", help="Write to a file instead of stdout")

    listing = sub.add_parser(
        "list",
        help="List the definitions in a catalog directory",
        formatter_class=formatter_class,
    )
    listing.add_argument("--dir", help="Directory (default: WORKFLOWS_DIR)")

    generate = sub.add_parser(
        "generate",
        help="Draft a definition from a description, checked before it is shown",
        formatter_class=formatter_class,
    )
    generate.add_argument("description", help="What the workflow should do (or change)")
    generate.add_argument(
        "--revise", metavar="FILE", help="Revise this definition instead"
    )
    generate.add_argument("--attempts", type=int, choices=range(1, 6), metavar="1-5")
    generate.add_argument("-o", "--output", help="Write the YAML to a file")

    secret = sub.add_parser(
        "trigger-secret",
        help="Print the signing key of one workflow's inbound trigger",
        formatter_class=formatter_class,
    )
    secret.add_argument("workflow_id", metavar="ID")

    sign = sub.add_parser(
        "trigger-sign",
        help="Print the X-Baselith-Signature header for a request body",
        formatter_class=formatter_class,
    )
    sign.add_argument("workflow_id", metavar="ID")
    sign.add_argument("--body-file", help="File holding the body (default: stdin)")
    return parser


__all__ = ["register_parser", "run_workflow"]
