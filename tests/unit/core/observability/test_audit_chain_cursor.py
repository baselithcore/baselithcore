"""A cursor over the audit trail: everything after a sequence number, oldest first.

``SQLiteAuditSink.query`` returned newest first with an offset, which suits a screen and
fails an exporter: a poller that ships the trail to a SIEM needs "everything after the
record I last shipped", in order, with no gap and no repeat, however many records arrive
between two polls. A cursor on the chain's own ``seq`` is that: it is monotonic,
unique and survives a retention purge (which removes a prefix of the chain, never a
middle link). The defaults are unchanged, so every existing caller still gets newest
first.
"""

from __future__ import annotations

import pytest

from baselith.core.observability.audit import AuditEvent, AuditEventType
from baselith.core.observability.audit_chain import AuditQuery, SQLiteAuditSink


@pytest.fixture
def sink(tmp_path):
    s = SQLiteAuditSink(tmp_path / "audit.db")
    yield s
    s.close()


async def _fill(sink, n: int, *, event_type=AuditEventType.CUSTOM) -> None:
    for i in range(1, n + 1):
        await sink.write(AuditEvent(event_type, action=f"a{i}"))


def _seqs(rows) -> list[int]:
    return [r["seq"] for r in rows]


def test_the_defaults_are_unchanged():
    query = AuditQuery()

    assert query.after_seq is None
    assert query.ascending is False


async def test_without_a_cursor_the_newest_come_first_as_before(sink):
    await _fill(sink, 5)

    assert _seqs(sink.query()) == [5, 4, 3, 2, 1]


async def test_ascending_returns_the_oldest_first(sink):
    await _fill(sink, 5)

    assert _seqs(sink.query(AuditQuery(ascending=True))) == [1, 2, 3, 4, 5]


async def test_a_cursor_returns_only_what_is_after_it(sink):
    await _fill(sink, 6)

    rows = sink.query(AuditQuery(after_seq=3, ascending=True))

    assert _seqs(rows) == [4, 5, 6]


async def test_a_cursor_of_zero_means_everything(sink):
    await _fill(sink, 3)

    assert _seqs(sink.query(AuditQuery(after_seq=0, ascending=True))) == [1, 2, 3]


async def test_a_cursor_past_the_head_is_an_empty_page(sink):
    await _fill(sink, 3)

    assert sink.query(AuditQuery(after_seq=3, ascending=True)) == []
    assert sink.query(AuditQuery(after_seq=99, ascending=True)) == []


async def test_a_cursor_without_ascending_is_still_newest_first(sink):
    await _fill(sink, 6)

    assert _seqs(sink.query(AuditQuery(after_seq=3))) == [6, 5, 4]


async def test_pages_follow_one_another_with_no_gap_and_no_repeat(sink):
    await _fill(sink, 11)
    seen: list[int] = []
    cursor = 0

    while True:
        page = sink.query(AuditQuery(after_seq=cursor, ascending=True, limit=4))
        if not page:
            break
        seen.extend(_seqs(page))
        cursor = page[-1]["seq"]

    assert seen == list(range(1, 12))


async def test_records_that_arrive_between_polls_are_picked_up_next_time(sink):
    await _fill(sink, 3)
    first = sink.query(AuditQuery(after_seq=0, ascending=True))
    cursor = first[-1]["seq"]

    await _fill(sink, 2)

    assert _seqs(sink.query(AuditQuery(after_seq=cursor, ascending=True))) == [4, 5]


async def test_the_cursor_composes_with_the_other_filters(sink):
    await _fill(sink, 3, event_type=AuditEventType.CUSTOM)
    await _fill(sink, 3, event_type=AuditEventType.AUTH_FAILED)

    rows = sink.query(
        AuditQuery(
            after_seq=2, ascending=True, event_type=AuditEventType.AUTH_FAILED.value
        )
    )

    assert _seqs(rows) == [4, 5, 6]


async def test_a_retention_purge_leaves_the_cursor_working(sink):
    await _fill(sink, 4)
    # a purge removes a prefix of the chain: simulate it, as the sink's own tests do
    sink._conn.execute("DELETE FROM audit_log WHERE seq <= 2")

    assert _seqs(sink.query(AuditQuery(after_seq=0, ascending=True))) == [3, 4]
    assert _seqs(sink.query(AuditQuery(after_seq=3, ascending=True))) == [4]


async def test_each_record_carries_the_hashes_an_exporter_needs(sink):
    await _fill(sink, 2)

    first, second = sink.query(AuditQuery(after_seq=0, ascending=True))

    assert second["prev_hash"] == first["entry_hash"]
    assert first["entry_hash"] and second["entry_hash"]
