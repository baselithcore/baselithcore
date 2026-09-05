"""Tests for the docs-vs-code consistency gate (``scripts/check_docs_consistency.py``)."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import check_docs_consistency as gate
from scripts.docs_consistency import known, scan

PAGE = Path("docs/page.md")


def test_extract_imports_handles_parenthesised_lists_and_comments() -> None:
    text = (
        "```python\n"
        "from core.prioritization import (\n"
        "    Task,          # one, two\n"
        "    PriorityQueue,\n"
        ")\n"
        "import core.events\n"
        "```\n"
    )
    claims = scan.extract_imports(PAGE, text)
    assert [(c.value, c.extra) for c in claims] == [
        ("core.prioritization", "Task,PriorityQueue"),
        ("core.events", ""),
    ]


def test_extract_paths_ignores_placeholders_and_finds_root_files() -> None:
    text = "See core/api/factory.py, plugins/<name>/plugin.py, docker-compose.prod.yml and tests/x.py."
    assert [c.value for c in scan.extract_paths(PAGE, text)] == [
        "core/api/factory.py",
        "tests/x.py",
        "docker-compose.prod.yml",
    ]


def test_extract_env_names_is_strict_only_in_dotenv_fences() -> None:
    text = (
        "```env\nAUTH_REQUIRED=true\nIMAGE=ghcr.io/x\n```\n"
        "```bash\nIMAGE=foo run\nexport CACHE_REDIS_URL=redis://x\n```\n"
        "Set `CACHE_TTL` or `X_REQUEST_ID`.\n"
    )
    names = {c.value for c in scan.extract_env_names(PAGE, text, ("AUTH_", "CACHE_"))}
    # single-word names (IMAGE, PATH) are never treated as settings
    assert names == {"AUTH_REQUIRED", "CACHE_REDIS_URL", "CACHE_TTL"}


def test_extract_cli_and_routes() -> None:
    text = "Run `baselith plugin marketplace publish .` then call `GET /runs/{run_id}/events`."
    assert [c.value for c in scan.extract_cli_invocations(PAGE, text)] == [
        "plugin marketplace publish"
    ]
    assert [c.value for c in scan.extract_routes(PAGE, text)] == [
        "GET /runs/{run_id}/events"
    ]
    assert scan.normalize_route("/runs/{run_id}/events/") == "/runs/{}/events"


def test_settings_fields_derive_prefixed_env_names(tmp_path: Path) -> None:
    (tmp_path / "core" / "config").mkdir(parents=True)
    (tmp_path / "core" / "config" / "demo.py").write_text(
        "class DemoConfig(BaseSettings):\n"
        '    model_config = SettingsConfigDict(env_prefix="DEMO_")\n'
        "    timeout: int = 5\n"
        "class DictConfig(BaseSettings):\n"
        '    model_config = {"env_prefix": "DICT_"}\n'
        "    depth: int = 1\n",
        encoding="utf-8",
    )
    names, prefixes = known.known_env_names(tmp_path)
    assert {"DEMO_TIMEOUT", "DICT_DEPTH"} <= names
    assert {"DEMO_", "DICT_", "BASELITH_"} <= set(prefixes)


def test_check_page_reports_missing_path_link_env_and_cli(tmp_path: Path) -> None:
    page = tmp_path / "docs" / "page.md"
    page.parent.mkdir(parents=True)
    page.write_text(
        "See [other](other.md) and core/nope.py.\n"
        "```env\nAUTH_NOPE=1\n```\n"
        "Run `baselith plugin explode`.\n"
    )
    findings = gate.check_page(
        page,
        page.read_text(),
        env_names={"AUTH_REQUIRED"},
        env_prefixes=("AUTH_",),
        cli={"plugin": {"list": {}}},
        routes=None,
        imports=False,
    )
    messages = "\n".join(findings)
    assert "other.md does not resolve" in messages
    assert "core/nope.py does not exist" in messages
    assert "AUTH_NOPE is not a known setting" in messages
    assert "`baselith plugin explode` is not a command" in messages


def test_skip_marker_disables_one_check(tmp_path: Path) -> None:
    page = tmp_path / "docs" / "page.md"
    page.parent.mkdir(parents=True)
    page.write_text(
        "<!-- docs-consistency: skip routes, cli -->\nCall `GET /nope` via `baselith zap`.\n"
    )
    findings = gate.check_page(
        page,
        page.read_text(),
        env_names=set(),
        env_prefixes=(),
        cli={},
        routes=set(),
        imports=False,
    )
    assert findings == []


def test_routes_accept_v1_alias_mounts_and_plugin_prefixes(tmp_path: Path) -> None:
    page = tmp_path / "docs" / "page.md"
    page.parent.mkdir(parents=True)
    page.write_text(
        "`GET /v1/chat`, `GET /plugins/{name}/static/app.js`, `GET /api/my-plugin/x`, `POST /-/reload`, `GET /gone`\n"
    )
    findings = gate.check_page(
        page,
        page.read_text(),
        env_names=set(),
        env_prefixes=(),
        cli=None,
        routes={"GET /chat", "MOUNT /plugins/{}/static"},
        imports=False,
    )
    assert findings == [f"{page}:1: route — GET /gone is not served by the app"]


@pytest.mark.slow
def test_repository_docs_pass_the_fast_checks() -> None:
    """The real docs tree must stay clean for paths, links, env vars and CLI commands."""
    findings, _notices = gate.run(imports=False, cli=True, routes=False)
    assert findings == []
