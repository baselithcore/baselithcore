"""Integration tests for SessionManager.

Validate the end-to-end lifecycle: create → send → bounded rotation → LRU
eviction. These tests exercise the bounded-history + LRU behavior with
realistic volume, which per-method unit tests don't cover.
"""

from __future__ import annotations

import pytest

from plugins.baselithbot.sessions import SessionManager, SessionMessage

pytestmark = pytest.mark.slow


class TestSessionLifecycle:
    def test_history_is_bounded_per_session(self) -> None:
        manager = SessionManager(history_limit=50)
        session = manager.create(title="bounded")
        for idx in range(200):
            manager.send(
                session.id,
                SessionMessage(role="user", content=f"msg-{idx}"),
            )
        history = manager.history(session.id, limit=500)
        assert len(history) == 50
        assert history[0].content == "msg-150"
        assert history[-1].content == "msg-199"

    def test_session_eviction_follows_insertion_order_when_cap_reached(
        self,
    ) -> None:
        manager = SessionManager(history_limit=10, max_sessions=3)
        first = manager.create(title="first")
        second = manager.create(title="second")
        third = manager.create(title="third")
        manager.create(title="fourth")

        assert manager.get(first.id) is None, "oldest session must be evicted"
        assert manager.get(second.id) is not None
        assert manager.get(third.id) is not None

    def test_send_to_unknown_session_raises(self) -> None:
        manager = SessionManager()
        with pytest.raises(KeyError, match="not found"):
            manager.send(
                "does-not-exist",
                SessionMessage(role="user", content="x"),
            )

    def test_reset_clears_history_but_keeps_session(self) -> None:
        manager = SessionManager()
        session = manager.create(title="keep")
        manager.send(session.id, SessionMessage(role="user", content="a"))
        manager.send(session.id, SessionMessage(role="user", content="b"))
        assert len(manager.history(session.id)) == 2

        manager.reset(session.id)
        assert manager.history(session.id) == []
        assert manager.get(session.id) is not None
