"""Consent records are tenant-scoped: subject ids are only unique in a tenant.

The consent log was keyed by subject id alone, so with the privacy routes a
``privacy:manage`` holder in one tenant could read, withdraw, export or erase
the consent of another tenant's subject that happened to share the id.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from baselith.core.context import reset_tenant_context, set_tenant_context
from baselith.core.privacy.consent import (
    ConsentRecord,
    ConsentService,
    InMemoryConsentStore,
    SQLiteConsentStore,
    subject_key,
)


@contextmanager
def tenant(tenant_id: str) -> Iterator[None]:
    token = set_tenant_context(tenant_id)
    try:
        yield
    finally:
        reset_tenant_context(token)


@pytest.fixture(params=["memory", "sqlite"])
def service(request, tmp_path: Path) -> ConsentService:
    if request.param == "memory":
        return ConsentService(InMemoryConsentStore())
    return ConsentService(SQLiteConsentStore(tmp_path / "consent.db"))


async def test_a_tenant_never_sees_another_tenants_consent(service) -> None:
    with tenant("acme"):
        await service.grant("s1", "news")
    with tenant("globex"):
        assert await service.active_purposes("s1") == []
        assert await service.history("s1") == []
        assert await service.export("s1") == []
        assert await service.withdraw("s1", "news") is None
        assert await service.erase("s1") == 0
    with tenant("acme"):
        assert await service.active_purposes("s1") == ["news"]


async def test_erasure_stays_inside_the_tenant(service) -> None:
    with tenant("acme"):
        await service.grant("s1", "news")
    with tenant("globex"):
        await service.grant("s1", "ads")
        assert await service.erase("s1") == 1
    with tenant("acme"):
        assert await service.active_purposes("s1") == ["news"]


async def test_records_carry_their_tenant(service) -> None:
    with tenant("acme"):
        record = await service.grant("s1", "news")
    assert record.tenant_id == "acme"
    assert record.to_dict()["tenant_id"] == "acme"
    assert ConsentRecord.from_dict(record.to_dict()).tenant_id == "acme"


async def test_default_tenant_reads_pre_scoping_records() -> None:
    """Records written before scoping are filed under the bare subject id."""
    store = InMemoryConsentStore()
    legacy = ConsentRecord.from_dict(
        {"id": "r1", "subject_id": "s1", "purpose": "news", "granted": True}
    )
    await store.append(legacy)
    service = ConsentService(store)
    with tenant("default"):
        assert await service.active_purposes("s1") == ["news"]
    with tenant("acme"):
        assert await service.active_purposes("s1") == []


def test_subject_key() -> None:
    assert subject_key("s1", "default") == "s1"
    assert subject_key("s1", "acme") == "acme\x1fs1"


@pytest.mark.parametrize("subject", ["acme\x1fs1", "a\nb", "x\x00"])
def test_a_subject_id_cannot_forge_another_tenants_key(subject) -> None:
    with pytest.raises(ValueError, match="control characters"):
        subject_key(subject, "default")
