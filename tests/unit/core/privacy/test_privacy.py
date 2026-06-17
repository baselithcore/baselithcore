"""Tests for the privacy / data-subject-request framework."""

import time

import pytest

from core.privacy import (
    DataProviderRegistry,
    DataSubjectService,
    DictDataProvider,
    RetentionProvider,
)


def _service():
    reg = DataProviderRegistry()
    fb = DictDataProvider("feedback")
    mem = DictDataProvider("memory")
    fb.add("s1", {"q": "hi", "created_at": time.time()})
    fb.add("s1", {"q": "old", "created_at": 0.0})
    mem.add("s1", {"note": "x", "created_at": time.time()})
    mem.add("s2", {"note": "y", "created_at": time.time()})
    reg.register(fb)
    reg.register(mem)
    return DataSubjectService(reg), fb, mem


class TestExport:
    @pytest.mark.asyncio
    async def test_aggregates_all_providers(self):
        svc, _, _ = _service()
        bundle = await svc.export_subject("s1")
        assert set(bundle.data) == {"feedback", "memory"}
        assert len(bundle.data["feedback"]) == 2
        assert len(bundle.data["memory"]) == 1

    @pytest.mark.asyncio
    async def test_unknown_subject_is_empty(self):
        svc, _, _ = _service()
        bundle = await svc.export_subject("ghost")
        assert bundle.data["feedback"] == []
        assert bundle.data["memory"] == []

    @pytest.mark.asyncio
    async def test_provider_failure_isolated(self):
        svc, _, _ = _service()

        class Broken:
            name = "broken"

            async def export(self, s):
                raise RuntimeError("boom")

            async def erase(self, s):
                raise RuntimeError("boom")

        svc.registry.register(Broken())
        bundle = await svc.export_subject("s1")
        assert bundle.data["broken"] == {"error": "export_failed"}
        # Other providers still exported.
        assert len(bundle.data["feedback"]) == 2


class TestErase:
    @pytest.mark.asyncio
    async def test_removes_and_counts(self):
        svc, _, _ = _service()
        report = await svc.erase_subject("s1")
        assert report.erased == {"feedback": 2, "memory": 1}
        assert report.total == 3
        # Gone afterward.
        bundle = await svc.export_subject("s1")
        assert bundle.data["feedback"] == []

    @pytest.mark.asyncio
    async def test_only_targets_subject(self):
        svc, _, mem = _service()
        await svc.erase_subject("s1")
        # s2's memory record is untouched.
        assert await mem.export("s2") != []

    @pytest.mark.asyncio
    async def test_erase_failure_isolated(self):
        svc, _, _ = _service()

        class Broken:
            name = "broken"

            async def export(self, s):
                return []

            async def erase(self, s):
                raise RuntimeError("boom")

        svc.registry.register(Broken())
        report = await svc.erase_subject("s1")
        assert report.erased["broken"] == 0
        assert report.erased["feedback"] == 2


class TestRetention:
    @pytest.mark.asyncio
    async def test_purges_old_records(self):
        svc, _, _ = _service()
        report = await svc.purge_expired(older_than_seconds=3600)
        # The created_at=0 feedback record is purged; fresh ones stay.
        assert report.purged["feedback"] == 1
        assert report.purged["memory"] == 0
        assert report.total == 1

    @pytest.mark.asyncio
    async def test_skips_non_retention_providers(self):
        reg = DataProviderRegistry()

        class NoPurge:
            name = "nopurge"

            async def export(self, s):
                return []

            async def erase(self, s):
                return 0

        reg.register(NoPurge())
        report = await DataSubjectService(reg).purge_expired(100)
        assert report.purged == {}


class TestRegistry:
    def test_register_get_all_unregister(self):
        reg = DataProviderRegistry()
        p = DictDataProvider("p")
        reg.register(p)
        assert reg.get("p") is p
        assert [x.name for x in reg.all()] == ["p"]
        assert reg.unregister("p") is True
        assert reg.get("p") is None

    def test_dict_provider_is_retention_provider(self):
        assert isinstance(DictDataProvider("p"), RetentionProvider)
