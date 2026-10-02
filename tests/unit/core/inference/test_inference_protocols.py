"""Customer-owned model servers: OpenAI / Cohere / NIM protocols, TLS, dim guard."""

from __future__ import annotations

import json
import ssl
from pathlib import Path

import certifi
import httpx
import pytest

from core.config.inference import EmbeddingConfig, RerankConfig
from core.services.inference import (
    EmbeddingService,
    InferenceConfigError,
    InferenceError,
    RerankService,
)
from core.services.inference._http import tls_context


def _emb(**kw: object) -> EmbeddingConfig:
    base = dict(
        url="https://gpu.customer.example/v1", api="openai", model="e5-large", dim=3
    )
    base.update(backoff_base=0, max_retries=0, batch_size=8)
    return EmbeddingConfig(**{**base, **kw})  # type: ignore[arg-type]


def _rr(**kw: object) -> RerankConfig:
    base = dict(
        url="https://gpu.customer.example/v1", model="rr", backoff_base=0, max_retries=0
    )
    return RerankConfig(**{**base, **kw})  # type: ignore[arg-type]


async def test_openai_embeddings_reorder_by_index_and_apply_prefixes() -> None:
    seen: dict[str, object] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["path"] = req.url.path
        body = json.loads(req.content)
        seen["body"] = body
        data = [
            {"index": i, "embedding": [float(i), 0.0, 1.0]}
            for i in range(len(body["input"]))
        ]
        return httpx.Response(200, json={"data": list(reversed(data))})

    svc = EmbeddingService.from_config(
        _emb(query_prefix="query: ", document_prefix="passage: "),
        transport=httpx.MockTransport(handler),
    )
    vecs = await svc.embed_documents(["a", "b"])
    assert seen["path"] == "/v1/embeddings"
    assert seen["body"] == {
        "model": "e5-large",
        "input": ["passage: a", "passage: b"],
        "encoding_format": "float",
    }
    assert [v[0] for v in vecs] == [0.0, 1.0]
    await svc.embed_query("q")
    assert seen["body"]["input"] == ["query: q"]  # type: ignore[index]


async def test_custom_path_overrides_the_protocol_default() -> None:
    paths: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        paths.append(req.url.path)
        return httpx.Response(
            200, json={"data": [{"index": 0, "embedding": [1.0, 1.0, 1.0]}]}
        )

    svc = EmbeddingService.from_config(
        _emb(url="https://gw.example", path="/ai/emb"),
        transport=httpx.MockTransport(handler),
    )
    await svc.embed_query("q")
    assert paths == ["/ai/emb"]


async def test_wrong_dimension_fails_with_the_fix() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"data": [{"index": 0, "embedding": [1.0] * 768}]}
        )

    svc = EmbeddingService.from_config(
        _emb(dim=1024), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(InferenceError, match="768-dim.*BASELITH_EMBEDDING_DIM"):
        await svc.embed_query("q")


async def test_malformed_openai_body_is_an_inference_error() -> None:
    svc = EmbeddingService.from_config(
        _emb(),
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"object": "x"})
        ),
    )
    with pytest.raises(InferenceError, match="no 'data'"):
        await svc.embed_query("q")


async def test_cohere_style_rerank() -> None:
    seen: dict[str, object] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["path"] = req.url.path
        body = json.loads(req.content)
        seen["body"] = body
        results = [
            {"index": i, "relevance_score": float(len(d))}
            for i, d in enumerate(body["documents"])
        ]
        return httpx.Response(
            200, json={"results": sorted(results, key=lambda r: -r["relevance_score"])}
        )

    svc = RerankService.from_config(
        _rr(api="cohere"), transport=httpx.MockTransport(handler)
    )
    out = await svc.rerank("q", ["a", "ccc", "bb"], top_k=2)
    assert seen["path"] == "/v1/rerank"
    assert seen["body"] == {
        "model": "rr",
        "query": "q",
        "documents": ["a", "ccc", "bb"],
        "top_n": 3,
    }
    assert out == [(1, 3.0), (2, 2.0)]


async def test_nim_ranking_maps_logits_to_unit_scores() -> None:
    seen: dict[str, object] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["path"] = req.url.path
        seen["body"] = json.loads(req.content)
        return httpx.Response(
            200,
            json={
                "rankings": [{"index": 1, "logit": 4.0}, {"index": 0, "logit": -4.0}]
            },
        )

    svc = RerankService.from_config(
        _rr(api="nim"), transport=httpx.MockTransport(handler)
    )
    out = await svc.rerank("q", ["x", "y"], top_k=2)
    assert seen["path"] == "/v1/ranking"
    assert seen["body"]["passages"] == [{"text": "x"}, {"text": "y"}]  # type: ignore[index]
    assert out[0][0] == 1 and 0.98 < out[0][1] < 1.0 and 0.0 < out[1][1] < 0.02


async def test_incomplete_cohere_results_raise() -> None:
    svc = RerankService.from_config(
        _rr(api="cohere"),
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, json={"results": [{"index": 0, "relevance_score": 1.0}]}
            )
        ),
    )
    with pytest.raises(InferenceError, match="scored 1 of 2"):
        await svc.rerank("q", ["a", "b"], top_k=2)


def test_tls_defaults_to_the_system_store() -> None:
    assert tls_context(None, None, None) is True


def test_tls_trusts_a_private_ca_bundle() -> None:
    assert isinstance(tls_context(certifi.where(), None, None), ssl.SSLContext)


def test_tls_misconfiguration_is_caught_at_startup(tmp_path: Path) -> None:
    with pytest.raises(InferenceConfigError, match="ca_bundle not found"):
        tls_context(str(tmp_path / "missing.pem"), None, None)
    key = tmp_path / "k.pem"
    key.write_text("x")
    with pytest.raises(InferenceConfigError, match="needs client_cert"):
        tls_context(None, None, str(key))
    with pytest.raises(InferenceConfigError, match="ca_bundle not found"):
        EmbeddingService.from_config(_emb(ca_bundle=str(tmp_path / "nope.pem")))
