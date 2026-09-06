"""SDK ↔ OpenAPI contract gate.

The Python and TypeScript clients under ``sdk/`` are hand-written and cover a
deliberate subset of the API — chat, streaming chat, feedback, health,
readiness — not the full 100-route surface. Nothing checked that the subset was
still *real*: a route renamed or a method changed in ``core/`` left the clients
calling an endpoint that had stopped existing, and the failure surfaced in a
consumer's application, not in this repo's CI.

There is already an OpenAPI drift gate keeping ``sdk/openapi.json`` in step with
the app. This one closes the other half:

* every ``(method, path)`` a client calls exists in the committed spec;
* both clients call the **same** set, so the two SDKs cannot drift apart.

Paths are read from the client sources rather than from a generator manifest,
so adding a call without updating the spec fails here.

Usage:
    python scripts/check_sdk_contract.py
    python scripts/check_sdk_contract.py --list
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OPENAPI_PATH = REPO_ROOT / "sdk" / "openapi.json"
PYTHON_CLIENT = REPO_ROOT / "sdk" / "python" / "baselith_sdk" / "client.py"
TS_CLIENT = REPO_ROOT / "sdk" / "typescript" / "src" / "client.ts"

#: Version prefix the clients prepend for versioned calls.
API_PREFIX = "/v1"
#: Calls made against the unversioned root (probes).
UNVERSIONED = frozenset({"/health", "/health/ready"})

_TS_CALL_RE = re.compile(
    r"""request\(\s*['"](GET|POST|PUT|PATCH|DELETE)['"]\s*,\s*['"](/[^'"]*)['"]"""
)
# `rawFetch(this.url('/chat/stream'), { method: 'POST', ... })` — the verb sits
# in the options object, so it has to be read from there rather than assumed.
_TS_RAW_RE = re.compile(
    r"""rawFetch\(\s*this\.url\(\s*['"](/[^'"]*)['"]\s*\)[^)]*?method:\s*['"](GET|POST|PUT|PATCH|DELETE)['"]""",
    re.DOTALL,
)


def _spec_routes() -> set[tuple[str, str]]:
    """``{(METHOD, path)}`` declared by the committed OpenAPI document."""
    spec = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
    return {
        (method.upper(), path)
        for path, operations in spec.get("paths", {}).items()
        for method in operations
        if method.lower() in {"get", "post", "put", "patch", "delete"}
    }


def python_client_calls(path: Path = PYTHON_CLIENT) -> set[tuple[str, str]]:
    """``{(METHOD, path)}`` the Python client issues.

    Read from the AST. Two shapes matter: ``self._request("POST", "/chat", ...)``
    carries its verb inline, while the streaming helper assigns
    ``url = self._url("/chat/stream")`` and passes it to
    ``self._http.stream("POST", url, ...)`` — so the verb has to be taken from
    the streaming call, never assumed.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    calls: set[tuple[str, str]] = set()

    url_vars: dict[str, str] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and node.value.func.attr == "_url"
            and node.value.args
            and isinstance(node.value.args[0], ast.Constant)
            and isinstance(node.value.args[0].value, str)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            url_vars[node.targets[0].id] = node.value.args[0].value

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        consts = [
            a.value
            for a in node.args
            if isinstance(a, ast.Constant) and isinstance(a.value, str)
        ]
        if node.func.attr in {"_request", "request"} and len(consts) >= 2:
            calls.add((consts[0].upper(), consts[1]))
        elif node.func.attr == "stream" and consts and len(node.args) >= 2:
            target = node.args[1]
            if isinstance(target, ast.Name) and target.id in url_vars:
                calls.add((consts[0].upper(), url_vars[target.id]))
            elif (
                isinstance(target, ast.Call)
                and isinstance(target.func, ast.Attribute)
                and target.func.attr == "_url"
                and target.args
                and isinstance(target.args[0], ast.Constant)
            ):
                calls.add((consts[0].upper(), target.args[0].value))

    return {
        (method, route)
        for method, route in calls
        if route.startswith("/") and route != "/"
    }


def typescript_client_calls(path: Path = TS_CLIENT) -> set[tuple[str, str]]:
    """``{(METHOD, path)}`` the TypeScript client issues."""
    source = path.read_text(encoding="utf-8")
    calls = {(m.group(1).upper(), m.group(2)) for m in _TS_CALL_RE.finditer(source)}
    calls |= {(m.group(2).upper(), m.group(1)) for m in _TS_RAW_RE.finditer(source)}
    return calls


def resolve(route: str) -> str:
    """The spec path for a client route, applying the version prefix."""
    return route if route in UNVERSIONED else f"{API_PREFIX}{route}"


def check_sdk_contract() -> list[str]:
    """Return contract violations (empty when the SDKs match the spec)."""
    violations: list[str] = []
    spec = _spec_routes()
    python_calls = python_client_calls()
    ts_calls = typescript_client_calls()

    if not python_calls:
        violations.append(
            "no calls parsed from the Python client — the gate would pass vacuously"
        )
    if not ts_calls:
        violations.append(
            "no calls parsed from the TypeScript client — the gate would pass vacuously"
        )

    for label, calls in (("python", python_calls), ("typescript", ts_calls)):
        for method, route in sorted(calls):
            resolved = resolve(route)
            if (method, resolved) not in spec:
                violations.append(
                    f"{label} SDK calls {method} {resolved}, which the OpenAPI spec does "
                    "not declare — regenerate sdk/openapi.json or fix the client"
                )

    only_python = sorted(python_calls - ts_calls)
    only_ts = sorted(ts_calls - python_calls)
    if only_python:
        violations.append(f"only the Python SDK calls: {only_python}")
    if only_ts:
        violations.append(f"only the TypeScript SDK calls: {only_ts}")
    return violations


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--list", action="store_true", help="print the routes each client calls"
    )
    args = parser.parse_args(argv)

    if args.list:
        for label, calls in (
            ("python", python_client_calls()),
            ("typescript", typescript_client_calls()),
        ):
            print(f"{label}:")
            for method, route in sorted(calls):
                print(f"  {method:<6} {route}  ->  {resolve(route)}")
        return 0

    violations = check_sdk_contract()
    if violations:
        print("SDK contract violations:", file=sys.stderr)
        for violation in violations:
            print(f" - {violation}", file=sys.stderr)
        return 1

    covered = len(python_client_calls())
    print(f"SDK contract OK ({covered} route(s), Python and TypeScript in step).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
