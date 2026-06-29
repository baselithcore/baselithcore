"""Tests for Article 50 transparency wiring in the chat router."""

from __future__ import annotations

from pydantic import SecretStr

from core.models.chat import ChatResponse
from core.transparency import (
    DisclosureService,
    ProvenanceTagger,
    TransparencyService,
)
from core.transparency.provenance import PROVENANCE_HEADER
from plugins.api_routers.chat import _DISCLOSURE_HEADER, _apply_transparency


def _service(*, enabled: bool) -> TransparencyService:
    tagger = ProvenanceTagger("BaselithCore", signing_secret=SecretStr("k"))
    return TransparencyService(
        disclosure=DisclosureService(enabled=enabled), tagger=tagger
    )


def test_disabled_is_noop() -> None:
    resp = ChatResponse(answer="hi", metadata={"x": 1})
    headers = _apply_transparency(resp, _service(enabled=False))
    assert headers == {}
    assert resp.metadata == {"x": 1}  # untouched


def test_enabled_adds_disclosure_and_provenance() -> None:
    resp = ChatResponse(answer="generated answer")
    headers = _apply_transparency(resp, _service(enabled=True))

    # Art 50(1): disclosure in body metadata + header flag.
    assert headers[_DISCLOSURE_HEADER] == "true"
    assert resp.metadata is not None
    assert resp.metadata["ai_disclosure"]["machine_readable"] is True

    # Art 50(2): provenance header binds to the answer text.
    assert PROVENANCE_HEADER in headers
    tag = ProvenanceTagger.from_header_value(headers[PROVENANCE_HEADER])
    assert _service(enabled=True).verify_content(tag, "generated answer") is True


def test_existing_metadata_preserved() -> None:
    resp = ChatResponse(answer="a", metadata={"sources": 3})
    _apply_transparency(resp, _service(enabled=True))
    assert resp.metadata is not None
    assert resp.metadata["sources"] == 3
    assert "ai_disclosure" in resp.metadata
