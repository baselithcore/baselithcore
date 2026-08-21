"""Tests for GDPR Art. 7/16/18/21 — consent, rectification, restriction, objection."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from core.privacy.consent import ConsentRecord, ConsentService
from core.privacy.provider import DataProviderRegistry, DictDataProvider
from core.privacy.service import DataSubjectService
from core.privacy.types import ObjectionOutcome


class _ExportOnlyProvider:
    """A provider that supports neither rectification nor restriction."""

    name = "legacy"

    async def export(self, subject_id: str) -> dict[str, str]:
        return {"subject": subject_id}

    async def erase(self, subject_id: str) -> int:
        return 0


@pytest.fixture
def service():
    registry = DataProviderRegistry()
    provider = DictDataProvider("main")
    provider.add("s1", {"email": "old@example.test", "created_at": 0})
    registry.register(provider)
    return DataSubjectService(registry), provider


class TestRectification:
    async def test_corrections_are_applied(self, service):
        svc, provider = service
        report = await svc.rectify_subject("s1", {"email": "new@example.test"})
        assert report.total == 1
        assert (await provider.export("s1"))[0]["email"] == "new@example.test"

    async def test_unsupported_providers_are_named_not_skipped(self):
        registry = DataProviderRegistry()
        registry.register(_ExportOnlyProvider())
        report = await DataSubjectService(registry).rectify_subject("s1", {"a": 1})
        assert report.unsupported == ["legacy"]
        assert report.total == 0

    async def test_a_failing_provider_does_not_abort_the_others(self):
        class Boom(DictDataProvider):
            async def rectify(self, subject_id, corrections):
                raise RuntimeError("store offline")

        registry = DataProviderRegistry()
        broken = Boom("broken")
        broken.add("s1", {"email": "x"})
        healthy = DictDataProvider("healthy")
        healthy.add("s1", {"email": "x"})
        registry.register(broken)
        registry.register(healthy)

        report = await DataSubjectService(registry).rectify_subject(
            "s1", {"email": "y"}
        )
        assert report.rectified["broken"] == 0
        assert report.rectified["healthy"] == 1


class TestRestriction:
    async def test_restriction_flags_without_erasing(self, service):
        svc, provider = service
        report = await svc.restrict_subject("s1")
        assert report.restricted is True
        assert report.total == 1
        assert provider.is_restricted("s1") is True
        # Art. 18 is not Art. 17 — the data is still there.
        assert len(await provider.export("s1")) == 1

    async def test_restriction_can_be_released(self, service):
        svc, provider = service
        await svc.restrict_subject("s1")
        report = await svc.restrict_subject("s1", restricted=False)
        assert report.restricted is False
        assert provider.is_restricted("s1") is False

    async def test_unsupported_providers_are_named(self):
        registry = DataProviderRegistry()
        registry.register(_ExportOnlyProvider())
        report = await DataSubjectService(registry).restrict_subject("s1")
        assert report.unsupported == ["legacy"]


class TestObjection:
    async def test_objection_without_grounds_is_upheld_and_restricts(self, service):
        svc, provider = service
        record = await svc.record_objection("s1", processing="profiling")
        assert record.outcome is ObjectionOutcome.UPHELD
        assert record.restriction is not None
        assert provider.is_restricted("s1") is True

    async def test_compelling_grounds_override_a_general_objection(self, service):
        svc, provider = service
        record = await svc.record_objection(
            "s1",
            processing="fraud detection",
            override_grounds="Statutory anti-fraud duty under national law.",
        )
        assert record.outcome is ObjectionOutcome.OVERRIDDEN
        assert record.restriction is None
        assert provider.is_restricted("s1") is False

    async def test_direct_marketing_objection_is_absolute(self, service):
        svc, provider = service
        # Patch the module logger instead of capturing stdout: the global
        # structlog sink is process-wide mutable state, so a stdout assert
        # is order-dependent under random test ordering.
        with patch("core.privacy.service.logger") as log:
            record = await svc.record_objection(
                "s1",
                processing="newsletter",
                direct_marketing=True,
                override_grounds="we really want to keep mailing them",
            )
        # Art. 21(2)/(3) admits no override.
        assert record.outcome is ObjectionOutcome.UPHELD
        assert record.override_grounds is None
        assert provider.is_restricted("s1") is True
        logged = " ".join(str(c) for c in log.warning.call_args_list)
        assert "objection override ignored" in logged

    async def test_resolution_is_timestamped(self, service):
        svc, _ = service
        record = await svc.record_objection("s1")
        assert record.resolved_at is not None


class TestConsent:
    async def test_grant_then_check(self):
        svc = ConsentService()
        await svc.grant("s1", "marketing", notice_version="v3", evidence="signup-form")
        assert await svc.has_consent("s1", "marketing") is True
        assert await svc.has_consent("s1", "analytics") is False

    async def test_withdrawal_appends_rather_than_deletes(self):
        svc = ConsentService()
        await svc.grant("s1", "marketing")
        await svc.withdraw("s1", "marketing")
        assert await svc.has_consent("s1", "marketing") is False
        # Art. 7(3): prior lawfulness must remain demonstrable.
        history = await svc.history("s1")
        assert len(history) == 2
        assert history[0].granted is True

    async def test_withdrawing_absent_consent_is_a_noop(self):
        svc = ConsentService()
        assert await svc.withdraw("s1", "marketing") is None

    async def test_consent_can_be_regranted(self):
        svc = ConsentService()
        await svc.grant("s1", "marketing")
        await svc.withdraw("s1", "marketing")
        await svc.grant("s1", "marketing", notice_version="v4")
        assert await svc.has_consent("s1", "marketing") is True
        assert len(await svc.history("s1")) == 3

    async def test_active_purposes_reflect_the_latest_state(self):
        svc = ConsentService()
        await svc.grant("s1", "analytics")
        await svc.grant("s1", "marketing")
        await svc.withdraw("s1", "marketing")
        assert await svc.active_purposes("s1") == ["analytics"]

    async def test_service_acts_as_a_data_provider(self):
        svc = ConsentService()
        await svc.grant("s1", "marketing", notice_version="v3")
        exported = await svc.export("s1")
        assert exported[0]["purpose"] == "marketing"
        assert exported[0]["notice_version"] == "v3"
        assert await svc.erase("s1") == 1
        assert await svc.export("s1") == []

    async def test_registering_consent_in_the_dsr_registry(self):
        registry = DataProviderRegistry()
        consent = ConsentService()
        await consent.grant("s1", "marketing")
        registry.register(consent)
        bundle = await DataSubjectService(registry).export_subject("s1")
        assert bundle.data["consent"][0]["purpose"] == "marketing"

    def test_record_round_trips_through_its_dict_payload(self):
        record = ConsentRecord(subject_id="s1", purpose="p", notice_version="v1")
        assert ConsentRecord.from_dict(record.to_dict()).to_dict() == record.to_dict()
