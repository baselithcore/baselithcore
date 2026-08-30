"""Integration tests for PostgresPatternStore.

Requires a real Postgres (``BASELITH_TEST_REAL_DB=1`` + Docker Postgres
up); psycopg is globally mocked in unit runs and CI never sets the flag,
so these skip everywhere except an explicit local integration run.
"""

from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("BASELITH_TEST_REAL_DB"),
    reason="real Postgres required (BASELITH_TEST_REAL_DB=1)",
)


def _pattern(fingerprint: str):
    from core.skill_evolution.types import Pattern, PatternKind

    return Pattern(
        fingerprint=fingerprint,
        kind=PatternKind.FAILURE_MODE,
        title=f"Failure {fingerprint}",
        summary="boom",
    )


@pytest.fixture
def unique_fp() -> str:
    return f"fp-{uuid.uuid4().hex[:12]}"


async def test_upsert_dedups_by_fingerprint(unique_fp: str) -> None:
    from core.skill_evolution.store_postgres import PostgresPatternStore

    store = PostgresPatternStore()
    first = await store.upsert(_pattern(unique_fp))
    second = await store.upsert(_pattern(unique_fp))
    assert second.id == first.id
    assert second.occurrences == 2


async def test_get_and_status_round_trip(unique_fp: str) -> None:
    from core.skill_evolution.store_postgres import PostgresPatternStore
    from core.skill_evolution.types import PatternStatus

    store = PostgresPatternStore()
    saved = await store.upsert(_pattern(unique_fp))
    assert (await store.get(saved.id)) is not None
    assert await store.set_status(saved.id, PatternStatus.PROMOTED) is True
    got = await store.get(saved.id)
    assert got is not None and got.status is PatternStatus.PROMOTED


async def test_list_orders_by_occurrences(unique_fp: str) -> None:
    from core.skill_evolution.store_postgres import PostgresPatternStore

    store = PostgresPatternStore()
    other = f"fp-{uuid.uuid4().hex[:12]}"
    await store.upsert(_pattern(unique_fp))
    await store.upsert(_pattern(unique_fp))
    await store.upsert(_pattern(other))
    listed = await store.list_patterns(limit=500)
    fingerprints = [p.fingerprint for p in listed]
    assert fingerprints.index(unique_fp) < fingerprints.index(other)
