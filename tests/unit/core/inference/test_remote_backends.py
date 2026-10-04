"""Remote TEI backends: batching, retry/backoff, timeouts, errors."""

from __future__ import annotations

import json

import httpx
import pytest

from core.config.inference import EmbeddingConfig, RerankConfig
from core.services.inference import (
    EmbeddingService,
    InferenceConfigError,
    InferenceError,
    RerankService,
)


def _emb_cfg(**kw: object) -> EmbeddingConfig:
    base = dict(url="http://tei", batch_size=2, max_retries=2, backoff_base=0, dim=3)
    return EmbeddingConfig(**{**base, **kw})  # type: ignore[arg-type]


def _rr_cfg(**kw: object) -> RerankConfig:
    base = dict(
        url="http://rr", batch_size=2, max_retries=2, backoff_base=0, max_candidates=5
    )
    return RerankConfig(**{**base, **kw})  # type: ignore[arg-type]


async def test_embed_documents_batches_in_order() -> None:
    seen: list[list[str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/embed"
        inputs = json.loads(req.content)["inputs"]
        seen.append(inputs)
        return httpx.Response(200, json=[[float(len(t)), 0.0, 0.0] for t in inputs])

    svc = EmbeddingService.from_config(
        _emb_cfg(), transport=httpx.MockTransport(handler)
    )
    out = await svc.embed_documents(["a", "bb", "ccc", "dddd", "e"])
    assert seen == [["a", "bb"], ["ccc", "dddd"], ["e"]]
    assert [v[0] for v in out] == [1.0, 2.0, 3.0, 4.0, 1.0]
    assert svc.model == "BAAI/bge-m3" and svc.dim == 3
    await svc.shutdown()


async def test_embed_query_single_vector() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[[1.0, 2.0, 3.0]])

    svc = EmbeddingService.from_config(
        _emb_cfg(), transport=httpx.MockTransport(handler)
    )
    assert await svc.embed_query("q") == [1.0, 2.0, 3.0]


async def test_retry_on_5xx_then_success() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json=[[0.0, 0.0, 1.0]])

    svc = EmbeddingService.from_config(
        _emb_cfg(), transport=httpx.MockTransport(handler)
    )
    assert await svc.embed_query("x") == [0.0, 0.0, 1.0]
    assert calls["n"] == 3


async def test_retry_exhausted_raises() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500)

    svc = EmbeddingService.from_config(
        _emb_cfg(), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(InferenceError, match="3 attempts"):
        await svc.embed_query("x")
    assert calls["n"] == 3


async def test_timeout_is_retried() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ReadTimeout("slow", request=req)
        return httpx.Response(200, json=[[1.0, 1.0, 1.0]])

    svc = EmbeddingService.from_config(
        _emb_cfg(), transport=httpx.MockTransport(handler)
    )
    assert await svc.embed_query("x") == [1.0, 1.0, 1.0]
    assert calls["n"] == 2


async def test_4xx_not_retried() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(413, text="too large")

    svc = EmbeddingService.from_config(
        _emb_cfg(), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(InferenceError, match="413"):
        await svc.embed_query("x")
    assert calls["n"] == 1


async def test_vector_count_mismatch_raises() -> None:
    svc = EmbeddingService.from_config(
        _emb_cfg(),
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[])),
    )
    with pytest.raises(InferenceError, match="vectors"):
        await svc.embed_query("x")


def test_remote_without_url_is_config_error() -> None:
    with pytest.raises(InferenceConfigError, match="BASELITH_EMBEDDING_URL"):
        EmbeddingService.from_config(EmbeddingConfig(url=None))
    with pytest.raises(InferenceConfigError, match="BASELITH_RERANK_URL"):
        RerankService.from_config(RerankConfig(url=None))


async def test_bearer_header_sent() -> None:
    got: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        got["auth"] = req.headers.get("authorization", "")
        return httpx.Response(200, json=[[0.0, 0.0, 0.0]])

    svc = EmbeddingService.from_config(
        _emb_cfg(url="https://tei", api_key="s3cret"),
        transport=httpx.MockTransport(handler),
    )
    await svc.embed_query("x")
    assert got["auth"] == "Bearer s3cret"


async def test_rerank_orders_caps_and_batches() -> None:
    batches: list[int] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        assert req.url.path == "/rerank" and body["query"] == "q"
        batches.append(len(body["texts"]))
        # TEI answers sorted by score, not input order.
        rows = [
            {"index": i, "score": float(len(t))} for i, t in enumerate(body["texts"])
        ]
        return httpx.Response(200, json=sorted(rows, key=lambda r: -r["score"]))

    svc = RerankService.from_config(_rr_cfg(), transport=httpx.MockTransport(handler))
    texts = ["a", "bbb", "cc", "dddd", "e", "ignored-beyond-cap"]
    out = await svc.rerank("q", texts, top_k=3)
    assert batches == [2, 2, 1]  # 5 candidates (cap), batched by 2
    assert out == [(3, 4.0), (1, 3.0), (2, 2.0)]


async def test_rerank_incomplete_scores_raise() -> None:
    svc = RerankService.from_config(
        _rr_cfg(),
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json=[{"index": 0, "score": 1.0}])
        ),
    )
    with pytest.raises(InferenceError, match="scored 1 of 2"):
        await svc.rerank("q", ["a", "b"], top_k=2)


# ── Transport hardening ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url", ["http://tei.internal:8080", "http://api.example.com", "http://10.0.0.5:80"]
)
def test_bearer_over_plain_http_to_a_possibly_public_host_is_refused(url: str) -> None:
    with pytest.raises(InferenceConfigError, match="https"):
        EmbeddingService.from_config(_emb_cfg(url=url, api_key="s3cret"))
    with pytest.raises(InferenceConfigError, match="https"):
        RerankService.from_config(_rr_cfg(url=url, api_key="s3cret"))


@pytest.mark.parametrize(
    "url",
    [
        "https://tei",
        "http://127.0.0.1:8080",
        "http://localhost:8080",
        "http://[::1]:80",
        "http://tei-embed:8080",  # compose service / same-namespace Service
        "http://rel-tei-embed.ml.svc:80",  # chart: <release>-tei-embed.<ns>.svc
        "http://rel-tei-rerank.ml.svc.cluster.local",
    ],
)
def test_bearer_over_https_loopback_or_cluster_internal_is_accepted(url: str) -> None:
    svc = EmbeddingService.from_config(_emb_cfg(url=url, api_key="s3cret"))
    assert svc is not None


def test_insecure_key_is_an_explicit_opt_out() -> None:
    cfg = _emb_cfg(
        url="http://api.example.com", api_key="s3cret", allow_insecure_key=True
    )
    assert EmbeddingService.from_config(cfg) is not None
    assert EmbeddingConfig().allow_insecure_key is False


def test_plain_http_without_a_key_is_still_fine() -> None:
    EmbeddingService.from_config(_emb_cfg(url="http://tei"))


def test_environment_proxies_are_ignored() -> None:
    svc = EmbeddingService.from_config(
        _emb_cfg(), transport=httpx.MockTransport(lambda r: httpx.Response(200))
    )
    assert svc._backend._http._client.trust_env is False  # type: ignore[attr-defined]


async def test_429_is_not_retried_by_default() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, text="queue full")

    svc = EmbeddingService.from_config(
        _emb_cfg(), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(InferenceError, match="429"):
        await svc.embed_query("x")
    assert calls["n"] == 1


async def test_429_retry_is_an_explicit_opt_in() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429)
        return httpx.Response(200, json=[[1.0, 0.0, 0.0]])

    svc = EmbeddingService.from_config(
        _emb_cfg(retry_rate_limited=True), transport=httpx.MockTransport(handler)
    )
    assert await svc.embed_query("x") == [1.0, 0.0, 0.0]
    assert calls["n"] == 2


async def test_upstream_body_is_not_echoed_into_the_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="internal path /srv/models/leak")

    svc = EmbeddingService.from_config(
        _emb_cfg(), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(InferenceError) as info:
        await svc.embed_query("x")
    assert "400" in str(info.value) and "leak" not in str(info.value)


async def test_oversize_response_is_refused() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[[0.123456] * 400])

    svc = EmbeddingService.from_config(
        _emb_cfg(max_response_bytes=1024), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(InferenceError, match="larger than 1024"):
        await svc.embed_query("x")


async def test_retries_stop_at_the_total_budget() -> None:
    import asyncio

    calls = {"n": 0}

    async def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        await asyncio.sleep(0.03)
        return httpx.Response(503)

    svc = EmbeddingService.from_config(
        _emb_cfg(max_retries=10, timeout=5.0, max_total_seconds=0.05),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(InferenceError, match="budget"):
        await svc.embed_query("x")
    assert 1 <= calls["n"] <= 3


def test_retry_budget_default_stays_under_the_edge_timeout() -> None:
    assert EmbeddingConfig().max_total_seconds < 60
    assert RerankConfig().max_total_seconds < 60
