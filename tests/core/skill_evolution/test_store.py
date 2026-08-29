"""Unit tests for the in-memory pattern store (wiki dedup semantics)."""

from __future__ import annotations

from core.skill_evolution.store import InMemoryPatternStore
from core.skill_evolution.types import (
    EvidenceRef,
    Pattern,
    PatternKind,
    PatternStatus,
)


def _pattern(fingerprint: str = "fp1", **overrides: object) -> Pattern:
    base: dict[str, object] = {
        "fingerprint": fingerprint,
        "kind": PatternKind.FAILURE_MODE,
        "title": f"Failure {fingerprint}",
        "summary": "boom",
    }
    base.update(overrides)
    return Pattern(**base)  # type: ignore[arg-type]


async def test_upsert_dedups_by_fingerprint() -> None:
    store = InMemoryPatternStore()
    first = await store.upsert(_pattern("fp1"))
    second = await store.upsert(
        _pattern(
            "fp1",
            evidence=[EvidenceRef(run_id="r2", score=0.2), EvidenceRef(run_id="r3")],
        )
    )
    assert second.id == first.id
    assert second.occurrences == 2
    # ALL incoming evidence entries merge (parity with the Postgres backend)
    assert [e.run_id for e in second.evidence] == ["r2", "r3"]
    assert len(await store.list_patterns()) == 1


async def test_upsert_with_empty_evidence_fabricates_nothing() -> None:
    store = InMemoryPatternStore()
    await store.upsert(_pattern("fp1"))
    merged = await store.upsert(_pattern("fp1"))
    assert merged.occurrences == 2
    assert merged.evidence == []


async def test_upsert_distinct_fingerprints() -> None:
    store = InMemoryPatternStore()
    await store.upsert(_pattern("fp1"))
    await store.upsert(_pattern("fp2"))
    assert len(await store.list_patterns()) == 2


async def test_get() -> None:
    store = InMemoryPatternStore()
    saved = await store.upsert(_pattern("fp1"))
    assert (await store.get(saved.id)) == saved
    assert (await store.get("missing")) is None


async def test_list_filters_and_ordering() -> None:
    store = InMemoryPatternStore()
    await store.upsert(_pattern("fp1"))
    await store.upsert(_pattern("fp1"))  # occurrences -> 2
    await store.upsert(_pattern("fp2", kind=PatternKind.STRATEGY))
    await store.upsert(_pattern("fp3"))

    listed = await store.list_patterns()
    assert listed[0].fingerprint == "fp1"  # highest occurrences first

    failures = await store.list_patterns(kind=PatternKind.FAILURE_MODE)
    assert {p.fingerprint for p in failures} == {"fp1", "fp3"}

    assert await store.list_patterns(status=PatternStatus.PROMOTED) == []
    assert len(await store.list_patterns(limit=1)) == 1


async def test_set_status() -> None:
    store = InMemoryPatternStore()
    saved = await store.upsert(_pattern("fp1"))
    assert await store.set_status(saved.id, PatternStatus.PROMOTED) is True
    got = await store.get(saved.id)
    assert got is not None and got.status is PatternStatus.PROMOTED
    assert await store.set_status("missing", PatternStatus.RETIRED) is False
