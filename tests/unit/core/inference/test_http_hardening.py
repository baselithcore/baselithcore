"""Inference HTTP hardening: the bearer stays on its host, bodies fail typed."""

from __future__ import annotations

import httpx
import pytest
from pydantic import ValidationError

from core.config.inference import EmbeddingConfig, RerankConfig
from core.services.inference import EmbeddingService, InferenceError


@pytest.mark.parametrize(
    "path",
    [
        "https://attacker.example/steal",
        "http://evil/x",
        "//evil.example/embed",
        "ftp://x/y",
    ],
)
@pytest.mark.parametrize("config", [EmbeddingConfig, RerankConfig])
def test_absolute_request_path_is_refused(
    config: type[EmbeddingConfig] | type[RerankConfig], path: str
) -> None:
    """An absolute path would make httpx ignore the base URL — and send the
    bearer token to whatever host the path names."""
    with pytest.raises(ValidationError, match="relative"):
        config(url="https://tei.internal", path=path)


@pytest.mark.parametrize("path", ["/embed", "v1/embeddings", "/v1/rerank?x=1", None])
def test_relative_request_path_is_accepted(path: str | None) -> None:
    assert EmbeddingConfig(url="https://tei", path=path).path == path


async def test_non_json_success_body_is_an_inference_error() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>proxy login</html>")

    svc = EmbeddingService.from_config(
        EmbeddingConfig(url="http://tei", dim=3, max_retries=0),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(InferenceError, match="not valid JSON"):
        await svc.embed_documents(["a"])
    await svc.shutdown()


async def test_embed_queries_applies_the_query_prefix_in_batches() -> None:
    seen: list[list[str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        import json

        inputs = json.loads(req.content)["inputs"]
        seen.append(inputs)
        return httpx.Response(200, json=[[1.0, 0.0, 0.0] for _ in inputs])

    svc = EmbeddingService.from_config(
        EmbeddingConfig(
            url="http://tei",
            dim=3,
            batch_size=2,
            query_prefix="query: ",
            document_prefix="passage: ",
        ),
        transport=httpx.MockTransport(handler),
    )
    out = await svc.embed_queries(["a", "b", "c"])
    assert len(out) == 3
    assert seen == [["query: a", "query: b"], ["query: c"]]
    await svc.shutdown()
