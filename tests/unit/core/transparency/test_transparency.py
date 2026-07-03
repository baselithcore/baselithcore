"""Unit tests for the AI transparency subsystem (EU AI Act Article 50)."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from core.transparency import (
    DEFAULT_DISCLOSURE_TEXT,
    ContentClass,
    DisclosureService,
    Modality,
    ProvenanceTag,
    ProvenanceTagger,
    TransparencyError,
    TransparencyService,
    sha256_hex,
)
from core.transparency.provenance import PROVENANCE_HEADER

# --------------------------------------------------------------------------- #
# Disclosure (Art 50(1))
# --------------------------------------------------------------------------- #


class TestDisclosure:
    def test_notice_uses_default_text_and_provider(self) -> None:
        svc = DisclosureService(provider="Acme")
        notice = svc.notice()
        assert notice.text == DEFAULT_DISCLOSURE_TEXT
        assert notice.provider == "Acme"
        assert notice.machine_readable is True
        assert notice.to_dict()["ai_disclosure"] == DEFAULT_DISCLOSURE_TEXT

    def test_should_disclose_when_enabled(self) -> None:
        assert DisclosureService(enabled=True).should_disclose() is True

    def test_disabled_suppresses_disclosure(self) -> None:
        assert DisclosureService(enabled=False).should_disclose() is False

    def test_obvious_context_exempts(self) -> None:
        # Art 50(1) exemption: no notice when AI use is obvious.
        assert DisclosureService(enabled=True).should_disclose(obvious=True) is False

    def test_custom_text(self) -> None:
        svc = DisclosureService(text="Parli con una AI.")
        assert svc.notice().text == "Parli con una AI."


# --------------------------------------------------------------------------- #
# Provenance (Art 50(2)/(4))
# --------------------------------------------------------------------------- #


class TestProvenance:
    def test_mark_computes_content_hash(self) -> None:
        tagger = ProvenanceTagger("BaselithCore")
        tag = tagger.mark("hello world", model="claude")
        assert tag.content_sha256 == sha256_hex("hello world")
        assert tag.content_class is ContentClass.AI_GENERATED
        assert tag.model == "claude"
        assert tag.is_synthetic is True
        assert tag.signature is None  # no secret configured

    def test_human_content_not_synthetic(self) -> None:
        tag = ProvenanceTagger("BaselithCore").mark(
            "x", content_class=ContentClass.HUMAN
        )
        assert tag.is_synthetic is False

    def test_modified_content_is_synthetic(self) -> None:
        tag = ProvenanceTagger("BaselithCore").mark(
            "x", content_class=ContentClass.AI_MODIFIED, modality=Modality.IMAGE
        )
        assert tag.is_synthetic is True
        assert tag.modality is Modality.IMAGE

    def test_unsigned_tag_verifies_on_hash_match(self) -> None:
        tagger = ProvenanceTagger("BaselithCore")
        tag = tagger.mark("data")
        assert tagger.verify(tag, "data") is True
        assert tagger.verify(tag, "tampered") is False

    def test_signed_tag_roundtrip(self) -> None:
        tagger = ProvenanceTagger("BaselithCore", signing_secret=SecretStr("s3cr3t"))
        tag = tagger.mark("payload")
        assert tag.signature is not None
        assert tagger.verify(tag, "payload") is True

    def test_signed_tag_detects_content_tamper(self) -> None:
        tagger = ProvenanceTagger("BaselithCore", signing_secret=SecretStr("s3cr3t"))
        tag = tagger.mark("payload")
        assert tagger.verify(tag, "payload-altered") is False

    def test_signed_tag_detects_signature_forgery(self) -> None:
        tagger = ProvenanceTagger("BaselithCore", signing_secret=SecretStr("s3cr3t"))
        tag = tagger.mark("payload")
        tag.signature = "deadbeef"
        assert tagger.verify(tag, "payload") is False

    def test_signing_policy_rejects_unsigned_tag(self) -> None:
        # A verifier under a signing policy must reject a tag with no signature.
        signed = ProvenanceTagger("X", signing_secret=SecretStr("k"))
        unsigned_tag = ProvenanceTagger("X").mark("c")
        assert unsigned_tag.signature is None
        assert signed.verify(unsigned_tag, "c") is False

    def test_wrong_secret_fails_verification(self) -> None:
        a = ProvenanceTagger("X", signing_secret=SecretStr("key-a"))
        b = ProvenanceTagger("X", signing_secret=SecretStr("key-b"))
        tag = a.mark("c")
        assert b.verify(tag, "c") is False

    def test_header_roundtrip(self) -> None:
        tagger = ProvenanceTagger("BaselithCore", signing_secret=SecretStr("s"))
        tag = tagger.mark("content", model="m1")
        header_value = tagger.to_header_value(tag)
        restored = ProvenanceTagger.from_header_value(header_value)
        assert restored.content_sha256 == tag.content_sha256
        assert restored.signature == tag.signature
        assert restored.model == "m1"
        assert tagger.verify(restored, "content") is True

    def test_malformed_header_raises(self) -> None:
        with pytest.raises(TransparencyError):
            ProvenanceTagger.from_header_value("!!!not-base64!!!")

    def test_c2pa_assertion_maps_action(self) -> None:
        gen = ProvenanceTagger("BaselithCore").mark(
            "x", content_class=ContentClass.AI_GENERATED
        )
        assertion = gen.c2pa_assertion()
        actions = assertion["assertions"][0]["data"]["actions"]
        assert actions[0]["action"] == "c2pa.created"
        assert actions[0]["digitalSourceType"] == "trainedAlgorithmicMedia"
        hash_assertion = assertion["assertions"][1]["data"]
        assert hash_assertion["alg"] == "sha256"
        assert hash_assertion["hash"] == gen.content_sha256

    def test_c2pa_edited_action_for_modified(self) -> None:
        tag = ProvenanceTagger("BaselithCore").mark(
            "x", content_class=ContentClass.AI_MODIFIED
        )
        actions = tag.c2pa_assertion()["assertions"][0]["data"]["actions"]
        assert actions[0]["action"] == "c2pa.edited"


# --------------------------------------------------------------------------- #
# Service facade
# --------------------------------------------------------------------------- #


class TestTransparencyService:
    def _service(
        self, *, enabled: bool = True, secret: str | None = None
    ) -> TransparencyService:
        tagger = ProvenanceTagger(
            "BaselithCore",
            signing_secret=SecretStr(secret) if secret else None,
        )
        disclosure = DisclosureService(enabled=enabled)
        return TransparencyService(disclosure=disclosure, tagger=tagger)

    def test_mark_content_returns_signed_tag(self) -> None:
        svc = self._service(secret="k")
        tag = svc.mark_content("out", model="claude")
        assert isinstance(tag, ProvenanceTag)
        assert tag.signature is not None
        assert svc.verify_content(tag, "out") is True

    def test_disclosure_flow(self) -> None:
        svc = self._service(enabled=True)
        assert svc.should_disclose() is True
        assert svc.disclosure_notice().text == DEFAULT_DISCLOSURE_TEXT

    def test_disabled_disclosure(self) -> None:
        assert self._service(enabled=False).should_disclose() is False

    def test_provenance_header_pair(self) -> None:
        svc = self._service()
        tag = svc.mark_content("content")
        name, value = svc.provenance_header(tag)
        assert name == PROVENANCE_HEADER
        assert (
            ProvenanceTagger.from_header_value(value).content_sha256
            == tag.content_sha256
        )

    def test_mark_content_emits_audit_log(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import core.transparency.service as service_mod

        calls: list[str] = []
        monkeypatch.setattr(
            service_mod.logger,
            "info",
            lambda msg, *args, **kwargs: calls.append(msg),
        )
        self._service().mark_content("x", model="m")
        assert any("AUDIT | TRANSPARENCY | content marked" in m for m in calls)


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #


class TestTransparencyConfig:
    def test_defaults_are_opt_in(self) -> None:
        from core.config.transparency import TransparencyConfig

        cfg = TransparencyConfig()
        assert cfg.enabled is False
        assert cfg.claim_generator == "BaselithCore"
        assert cfg.disclosure_text == DEFAULT_DISCLOSURE_TEXT
        assert cfg.signing_secret is None

    def test_secret_is_wrapped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from core.config.transparency import TransparencyConfig

        monkeypatch.setenv("TRANSPARENCY_SIGNING_SECRET", "top-secret")
        cfg = TransparencyConfig()
        assert isinstance(cfg.signing_secret, SecretStr)
        assert "top-secret" not in repr(cfg)
        assert cfg.signing_secret.get_secret_value() == "top-secret"

    def test_get_service_singleton(self) -> None:
        from core.transparency.service import get_transparency_service

        assert get_transparency_service() is get_transparency_service()
