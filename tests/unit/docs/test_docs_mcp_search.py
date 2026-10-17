"""The documentation MCP server answers the way an agent asks.

The search used to count occurrences of the whole query string, so a question
phrased in words ("how do I create a plugin") matched nothing. These tests pin
the section-level ranking that replaced it, and the launch form a hardened
stdio client (``baselith.core.mcp`` refuses ``PYTHONPATH`` and unlisted
``-m`` modules) has to use: the script path.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.unit.docs.docs_mcp_support import SERVER_DIR, load, write_site

ranking = load("ranking")
DocsService = load("service").DocsService

_PAGES = {
    "plugins/creating.md": """# Creating Plugins

Plugins extend the framework without touching the core.

## Scaffold with the CLI

Run `baselith plugin create my_plugin` to create a plugin skeleton.

## Manifest

Every plugin ships a manifest with its version and an integrity hash.

### Signing for integrity {#integrity}

`baselith plugin sign` writes `integrity_sha256` into the manifest.
""",
    "core/observability.md": """# Observability

## Token-report observer seam

Call `register_token_sink` to observe every token report.

## Metrics

Prometheus metrics. Prometheus metrics. Prometheus metrics. Prometheus metrics.
""",
    "core/memory.md": """# Memory

## Agent memory

Agents remember facts across turns. Policies decide what is kept.

## Policy

One policy here.

## Retention

Facts expire.

## Recall

Facts are recalled.
""",
}

_NAV = """[project]
nav = [
  { "Plugins" = [ { "Creating" = "plugins/creating.md" } ] },
  { "Core Modules" = [
      { "Observability" = "core/observability.md" },
      { "Memory" = "core/memory.md" },
  ] },
]
"""


@pytest.fixture
async def service(tmp_path: Path) -> DocsService:
    write_site(tmp_path, _NAV, _PAGES)
    svc = DocsService(str(tmp_path))
    await svc.initialize()
    return svc


async def test_a_question_in_words_finds_the_section(service: DocsService) -> None:
    hits = await service.search("how do I create a plugin")
    assert hits, "a natural-language question must not come back empty"
    assert hits[0]["path"] == "plugins/creating.md"
    assert hits[0]["anchor"] == "scaffold-with-the-cli"


async def test_terms_count_on_their_own_not_as_one_phrase(service: DocsService) -> None:
    # No page contains the phrase; two sections hold all three words.
    hits = await service.search("plugin manifest integrity")
    assert {h["heading"] for h in hits[:2]} == {"Manifest", "Signing for integrity"}


async def test_an_identifier_matches_whole_and_by_its_parts(
    service: DocsService,
) -> None:
    for query in ("register_token_sink", "token sink"):
        hits = await service.search(query)
        assert hits[0]["anchor"] == "token-report-observer-seam", query


async def test_plurals_fold_onto_the_singular(service: DocsService) -> None:
    hits = await service.search("policies")
    assert hits and hits[0]["path"] == "core/memory.md"


async def test_a_heading_outranks_a_body_mention(service: DocsService) -> None:
    hits = await service.search("policy")
    assert hits[0]["heading"] == "Policy"


async def test_one_page_yields_at_most_two_hits(service: DocsService) -> None:
    hits = await service.search("facts", limit=10)
    assert [h["path"] for h in hits].count("core/memory.md") == 2


async def test_stopwords_alone_find_nothing(service: DocsService) -> None:
    assert await service.search("how do I") == []


async def test_section_scope_filters_before_ranking(service: DocsService) -> None:
    hits = await service.search("metrics plugin", section="Plugins")
    assert hits and {h["path"] for h in hits} == {"plugins/creating.md"}


async def test_a_hit_reads_back_with_get_doc_section(service: DocsService) -> None:
    hit = (await service.search("token sink"))[0]
    section = service.get_section(hit["path"], hit["anchor"])
    assert section is not None and "register_token_sink" in section["content"]


async def test_an_edited_page_is_searched_as_it_is_now(
    service: DocsService, tmp_path: Path
) -> None:
    assert await service.search("quokka") == []
    page = tmp_path / "docs" / "core" / "memory.md"
    page.write_text(page.read_text() + "\n## Quokka mode\n\nA quokka.\n")
    # A distinct mtime even on filesystems with coarse timestamps.
    stat = page.stat()
    os.utime(page, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000))
    hits = await service.search("quokka")
    assert hits and hits[0]["anchor"] == "quokka-mode"


def test_terms_keep_identifiers_and_strip_accents() -> None:
    assert ranking.terms("Modalità `LLM_STREAM_AGENT_TEXT`") == [
        "modalita",
        "llm_stream_agent_text",
        "llm",
        "stream",
        "agent",
        "text",
    ]


def test_the_server_starts_by_script_path_in_isolated_mode(tmp_path: Path) -> None:
    """``python -I <path>/main.py``: no PYTHONPATH, no ``-m``, no script dir on the path."""
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    stdin = "".join(json.dumps(r) + "\n" for r in requests)
    proc = subprocess.run(
        [sys.executable, "-I", str(SERVER_DIR / "main.py")],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=60,
        cwd=tmp_path,  # the server writes logs/ under its working directory
        # No model server here, and no vector cache written to the home dir.
        env={**os.environ, "DOCS_MCP_SEMANTIC": "off"},
        check=False,
    )
    replies = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    listing = next((r for r in replies if r.get("id") == 2), None)
    assert listing is not None, proc.stderr[-2000:]
    names = {tool["name"] for tool in listing["result"]["tools"]}
    assert {"search_docs", "get_doc_section", "get_doc_outline"} <= names
    # stdout is the protocol and the cwd is the checkout: logs go to stderr only.
    assert not (tmp_path / "logs").exists()


async def test_an_explicit_heading_id_is_the_anchor(service: DocsService) -> None:
    hit = next(
        h for h in await service.search("sign integrity") if h["anchor"] == "integrity"
    )
    assert hit["heading"] == "Signing for integrity"
    section = service.get_section(hit["path"], hit["anchor"])
    assert section is not None and "integrity_sha256" in section["content"]
