"""Fail when ``mkdocs-site/docs`` makes a claim the code does not back.

CLAUDE.md: "Code and docs ship together — no merge with stale docs." This is
the mechanical half of that rule. Every page is scanned for verifiable claims
and each one is checked against the repository:

- **imports** — ``from core.x import Y`` in Python fences must really import;
- **paths** — ``core/…``, ``plugins/…``, ``tests/…`` references must exist
  (tutorial scaffolds listed in :data:`ILLUSTRATIVE_PATH_PREFIXES` are exempt);
- **links** — relative ``.md`` links must resolve;
- **env** — ``NAME=`` lines in env fences and ``PREFIX_NAME`` tokens must be
  variables the settings classes, templates or code define;
- **cli** — ``baselith <cmd> <sub>`` chains must exist in the argparse tree;
- **routes** — ``METHOD /path`` pairs must be served by the app (opt-in).

A page that documents another service can opt out of one check with an HTML
comment such as ``<!-- docs-consistency: skip routes -->``.

Usage::

    python scripts/check_docs_consistency.py            # imports + paths + links + env + cli
    python scripts/check_docs_consistency.py --routes   # also verify REST routes (builds the app)
    python scripts/check_docs_consistency.py --fast     # pre-commit: skip imports and routes

The judgement calls (is a default quoted correctly? does the prose describe
the behaviour?) stay with review; this gate only removes the class of drift
that a script can prove.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.docs_consistency import known, scan  # noqa: E402

DOCS_ROOT = REPO_ROOT / "mkdocs-site" / "docs"

# Tutorial scaffolds the docs build up step by step; they never exist in-tree.
ILLUSTRATIVE_PATH_PREFIXES: tuple[str, ...] = (
    "plugins/my-plugin/",
    "plugins/my_plugin/",
    "plugins/weather_agent/",
    "plugins/weather-agent/",
    "plugins/<",
)
# Files the reader is told to create, sample output, or a name the text warns against.
ILLUSTRATIVE_PATHS: frozenset[str] = frozenset(
    {
        "tests/test_orders.py",
        "configs/plugins.dev.yaml",
        ".github/workflows/publish.yml",
        "docker-compose.override.yml",
    }
)
# Tutorial packages that only exist once the reader has built them.
ILLUSTRATIVE_IMPORT_PREFIXES: tuple[str, ...] = (
    "plugins.weather_agent",
    "plugins.my_plugin",
)
# Sample plugin CLI commands from the "add a CLI to your plugin" walkthrough.
ILLUSTRATIVE_CLI: frozenset[str] = frozenset({"my-feature"})
# Env names a tutorial plugin would derive from its own name.
ILLUSTRATIVE_ENV_PREFIXES: tuple[str, ...] = ("MY_PLUGIN_", "WEATHER_AGENT_")
# Route prefixes owned by plugins or tutorials; only checked when the app knows them.
PLUGIN_ROUTE_PREFIXES: tuple[str, ...] = ("/api/",)
# Endpoints of third-party services the docs quote (Prometheus lifecycle API).
EXTERNAL_ROUTE_PREFIXES: tuple[str, ...] = ("/-/",)
SKIP_MARKER_RE = re.compile(r"<!--\s*docs-consistency:\s*skip\s+([\w, ]+?)\s*-->")
CHECK_KINDS = frozenset({"imports", "paths", "links", "env", "cli", "routes"})


def skipped_checks(text: str) -> set[str]:
    """Check kinds the page opted out of via ``<!-- docs-consistency: skip … -->``."""
    kinds = {
        k.strip() for m in SKIP_MARKER_RE.finditer(text) for k in m.group(1).split(",")
    }
    return kinds & CHECK_KINDS


def _check_imports(rel: Path, text: str) -> list[str]:
    out = []
    for claim in scan.extract_imports(rel, text):
        if claim.value.startswith(ILLUSTRATIVE_IMPORT_PREFIXES):
            continue
        problem = known.import_ok(claim.value, [n for n in claim.extra.split(",") if n])
        if problem:
            out.append(f"{rel}:{claim.line}: import — {problem}")
    return out


def _check_paths(rel: Path, text: str) -> list[str]:
    out = []
    for claim in scan.extract_paths(rel, text):
        if claim.value in ILLUSTRATIVE_PATHS or claim.value.startswith(
            ILLUSTRATIVE_PATH_PREFIXES
        ):
            continue
        if not (REPO_ROOT / claim.value).exists():
            out.append(f"{rel}:{claim.line}: path — {claim.value} does not exist")
    return out


def _check_links(page: Path, rel: Path, text: str) -> list[str]:
    return [
        f"{rel}:{claim.line}: link — {claim.value} does not resolve"
        for claim in scan.extract_links(rel, text)
        if not (page.parent / claim.value).resolve().exists()
    ]


def _check_env(
    rel: Path, text: str, names: set[str], prefixes: tuple[str, ...]
) -> list[str]:
    return [
        f"{rel}:{claim.line}: env — {claim.value} is not a known setting"
        for claim in scan.extract_env_names(rel, text, prefixes)
        if claim.value not in names
        and not claim.value.startswith(ILLUSTRATIVE_ENV_PREFIXES)
    ]


def _check_cli(rel: Path, text: str, tree: dict[str, dict]) -> list[str]:
    out = []
    for claim in scan.extract_cli_invocations(rel, text):
        words = claim.value.split()
        if words[0] in ILLUSTRATIVE_CLI:
            continue
        node: dict | None = tree
        for depth, word in enumerate(words):
            if node is None:  # leaf command: trailing words are arguments
                break
            if word not in node:
                if depth == 0 or node:  # unknown command, or unknown sub-command
                    chain = " ".join(words[: depth + 1])
                    out.append(
                        f"{rel}:{claim.line}: cli — `baselith {chain}` is not a command"
                    )
                break
            node = node[word] or None
    return out


def _check_routes(rel: Path, text: str, routes: set[str]) -> list[str]:
    out = []
    mounts = tuple(r.split(" ", 1)[1] for r in routes if r.startswith("MOUNT "))
    for claim in scan.extract_routes(rel, text):
        method, path = claim.value.split(" ", 1)
        norm = scan.normalize_route(path)
        candidates = {f"{method} {norm}", f"WS {norm}"}
        candidates.add(
            f"{method} {norm[3:]}" if norm.startswith("/v1/") else f"{method} /v1{norm}"
        )
        if candidates & routes:
            continue
        if norm.startswith(PLUGIN_ROUTE_PREFIXES + EXTERNAL_ROUTE_PREFIXES):
            continue
        if any(norm == m or norm.startswith(m + "/") for m in mounts):
            continue
        out.append(
            f"{rel}:{claim.line}: route — {claim.value} is not served by the app"
        )
    return out


def check_page(
    page: Path,
    text: str,
    *,
    env_names: set[str],
    env_prefixes: tuple[str, ...],
    cli: dict[str, dict] | None,
    routes: set[str] | None,
    imports: bool,
) -> list[str]:
    """All findings for one page, as ``path:line: message`` strings."""
    try:
        rel = page.relative_to(REPO_ROOT)
    except ValueError:  # pages outside the repo (tests, ad-hoc trees)
        rel = page
    skip = skipped_checks(text)
    findings: list[str] = []
    if imports and "imports" not in skip:
        findings += _check_imports(rel, text)
    if "paths" not in skip:
        findings += _check_paths(rel, text)
    if "links" not in skip:
        findings += _check_links(page, rel, text)
    if "env" not in skip:
        findings += _check_env(rel, text, env_names, env_prefixes)
    if cli is not None and "cli" not in skip:
        findings += _check_cli(rel, text, cli)
    if routes is not None and "routes" not in skip:
        findings += _check_routes(rel, text, routes)
    return findings


def run(
    docs_root: Path = DOCS_ROOT,
    *,
    imports: bool = True,
    cli: bool = True,
    routes: bool = False,
) -> tuple[list[str], list[str]]:
    """``(findings, notices)`` over every page under ``docs_root``."""
    notices: list[str] = []
    env_names, env_prefixes = known.known_env_names(REPO_ROOT)
    cli_tree = known.cli_tree(REPO_ROOT) if cli else None
    if cli and cli_tree is None:
        notices.append(
            "CLI tree unavailable (core.cli not importable here) — cli check skipped"
        )
    route_set = known.known_routes(REPO_ROOT) if routes else None
    if routes and route_set is None:
        notices.append("app not constructible here — route check skipped")

    findings: list[str] = []
    for page in scan.iter_pages(docs_root):
        findings.extend(
            check_page(
                page,
                page.read_text(encoding="utf-8"),
                env_names=env_names,
                env_prefixes=env_prefixes,
                cli=cli_tree,
                routes=route_set,
                imports=imports,
            )
        )
    return sorted(findings), notices


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--routes",
        action="store_true",
        help="also verify METHOD /path claims (builds the app)",
    )
    parser.add_argument(
        "--no-imports", action="store_true", help="skip importing code samples"
    )
    parser.add_argument(
        "--no-cli", action="store_true", help="skip the `baselith …` command check"
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="pre-commit mode: paths, links, env and cli only",
    )
    args = parser.parse_args(argv)

    imports = not (args.no_imports or args.fast)
    routes = args.routes and not args.fast
    findings, notices = run(imports=imports, cli=not args.no_cli, routes=routes)

    for notice in notices:
        print(f"note: {notice}")
    if not findings:
        print("Docs consistency: OK")
        return 0
    print("\n".join(findings))
    print(
        f"\nDocs consistency: {len(findings)} finding(s). Fix the page or the code — never both silently."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
