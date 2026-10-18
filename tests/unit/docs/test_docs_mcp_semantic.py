"""The semantic half of the docs search: a question in Italian finds English docs.

The real model (bge-m3 behind TEI) is replaced by a tiny bilingual embedder:
each word maps to a concept, Italian and English words for the same idea to the
same one, and a text's vector counts its concepts. That is enough to pin what
the layer owes the search — cross-language recall, rank fusion with keywords,
falling back to keywords when the model server fails, and a disk cache that a
second process reuses instead of re-embedding the corpus.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
import pytest

from tests.unit.docs.docs_mcp_support import load, write_site

semantic_mod = load("semantic")
DocsService = load("service").DocsService
SemanticIndex = semantic_mod.SemanticIndex

_CONCEPTS = {
    "sign": "sign", "signing": "sign", "firmo": "sign", "firmare": "sign", "firma": "sign",
    "plugin": "plugin", "plugins": "plugin",
    "memory": "memory", "memoria": "memory", "remember": "memory",
    "agent": "agent", "agents": "agent", "agente": "agent",
    "metrics": "metrics", "metriche": "metrics", "prometheus": "metrics",
    "deploy": "deploy", "distribuire": "deploy", "kubernetes": "deploy",
}  # fmt: skip
_DIM = sorted(set(_CONCEPTS.values()))

_PAGES = {
    "plugins/signing.md": """# Signing

## Signing a plugin

`baselith plugin sign` writes the digest. Signing protects a plugin.
""",
    "core/memory.md": """# Memory

## Agent memory

Agents remember facts across turns.
""",
    "ops/metrics.md": """# Metrics

## Prometheus

Prometheus scrapes the metrics endpoint.
""",
    "ops/deploy.md": """# Deploy

## Kubernetes

Deploy on Kubernetes with the chart.
""",
}
_NAV = """[project]
nav = [
  { "Signing" = "plugins/signing.md" },
  { "Memory" = "core/memory.md" },
  { "Metrics" = "ops/metrics.md" },
  { "Deploy" = "ops/deploy.md" },
]
"""


class BilingualEmbedder:
    def __init__(self) -> None:
        self.documents = 0
        self.fail = False

    def _vector(self, text: str) -> list[float]:
        vec = np.zeros(len(_DIM), dtype=np.float32)
        for word in text.lower().replace("`", " ").split():
            concept = _CONCEPTS.get(word.strip(".,:?!>'\""))
            if concept is not None:
                vec[_DIM.index(concept)] += 1
        return vec.tolist()

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.documents += len(texts)
        return [self._vector(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        if self.fail:
            raise RuntimeError("model server down")
        return self._vector(text)


async def _service(
    tmp_path: Path, embedder: BilingualEmbedder, pages: dict[str, str] | None = None
) -> tuple[object, object]:
    site = tmp_path / "site"
    site.mkdir(exist_ok=True)
    write_site(site, _NAV, pages or _PAGES)
    index = SemanticIndex(embedder, "fake/bilingual", cache_dir=tmp_path / "cache")
    svc = DocsService(str(site), semantic=index)
    await svc.initialize()
    await index.wait()
    return svc, index


async def test_an_italian_question_finds_the_english_section(tmp_path: Path) -> None:
    svc, _ = await _service(tmp_path, BilingualEmbedder())
    hits = await svc.search("come firmo un plugin")
    assert hits and hits[0]["path"] == "plugins/signing.md"
    hits = await svc.search("memoria dell'agente")
    assert hits and hits[0]["anchor"] == "agent-memory"


async def test_keywords_alone_cannot_cross_the_language(tmp_path: Path) -> None:
    site = tmp_path / "site"
    site.mkdir()
    write_site(site, _NAV, _PAGES)
    svc = DocsService(str(site))
    await svc.initialize()
    assert await svc.search("memoria dell'agente") == []


async def test_both_lists_agreeing_beat_either_alone(tmp_path: Path) -> None:
    svc, _ = await _service(tmp_path, BilingualEmbedder())
    hits = await svc.search("prometheus metrics")
    assert hits[0]["path"] == "ops/metrics.md"


async def test_a_failing_model_server_leaves_keyword_search(tmp_path: Path) -> None:
    embedder = BilingualEmbedder()
    svc, _ = await _service(tmp_path, embedder)
    embedder.fail = True
    assert (await svc.search("kubernetes"))[0]["path"] == "ops/deploy.md"
    assert await svc.search("distribuire") == []


async def test_a_second_process_reuses_the_cached_vectors(tmp_path: Path) -> None:
    first = BilingualEmbedder()
    await _service(tmp_path, first)
    assert first.documents > 0
    second = BilingualEmbedder()
    svc, _ = await _service(tmp_path, second)
    assert second.documents == 0
    assert (await svc.search("firmare"))[0]["path"] == "plugins/signing.md"


async def test_an_edited_section_alone_is_re_embedded(tmp_path: Path) -> None:
    await _service(tmp_path, BilingualEmbedder())
    edited = {
        **_PAGES,
        "ops/deploy.md": _PAGES["ops/deploy.md"] + "Also with agents.\n",
    }
    again = BilingualEmbedder()
    _, index = await _service(tmp_path, again, edited)
    assert again.documents == 1
    assert index.size == 4  # the stale vector of the old section was dropped


async def test_concurrent_builds_embed_the_corpus_once(tmp_path: Path) -> None:
    embedders = [BilingualEmbedder() for _ in range(3)]
    await asyncio.gather(*(_service(tmp_path, e) for e in embedders))
    assert sum(e.documents for e in embedders) == 4  # one section per page


def test_embed_text_is_breadcrumb_heading_and_opening() -> None:
    ranking = load("ranking")
    sections = load("sections")
    content = "# T\n\n## Signing a plugin\n\n`baselith plugin sign` writes it.\n"
    page = type(
        "P",
        (),
        {
            "path": "p.md",
            "title": "Plugins > Signing",
            "content": content,
            "sections": sections.parse_sections(content),
        },
    )()
    unit = ranking.Index([page]).units[-1]
    text = semantic_mod.embed_text(unit)
    assert text.startswith("Plugins > Signing > Signing a plugin\n")
    assert "`" not in text


@pytest.mark.parametrize("value", ["off", "0", "false"])
def test_the_layer_can_be_switched_off(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOCS_MCP_SEMANTIC", value)
    assert semantic_mod.build_embedder() is None


class FlakyEmbedder(BilingualEmbedder):
    """Refuses any batch holding the Kubernetes section, and that section alone."""

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if any("Kubernetes" in t for t in texts):
            raise RuntimeError("413 batch too large")
        return await super().embed_documents(texts)


async def test_one_bad_section_does_not_sink_the_build(tmp_path: Path) -> None:
    svc, index = await _service(tmp_path, FlakyEmbedder())
    assert index.size == 3  # every section but the refused one
    assert (await svc.search("firmare"))[0]["path"] == "plugins/signing.md"
    # The next build (a restart) tries the skipped section again, and only it.
    healthy = BilingualEmbedder()
    _, index = await _service(tmp_path, healthy)
    assert healthy.documents == 1
    assert index.size == 4
