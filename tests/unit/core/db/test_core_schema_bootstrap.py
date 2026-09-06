"""Core owns its schema, independent of what plugins declare.

The app's startup path initializes Postgres only when a *plugin* lists it as a
required resource (``ResourceAnalyzer``). Core's own tables — ``chat_feedback``,
``interactions``, ``feedback``, ``tenants`` — are not a plugin's concern, so on a
deployment whose enabled plugins merely list Postgres as *optional* the Alembic
upgrade never ran: the app reported healthy and the first core write failed with
``relation "chat_feedback" does not exist``.

These tests pin the decision and the failure mode of the fallback.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.db.schema import init_core_schema_best_effort, should_run_core_schema_init


class TestShouldRunCoreSchemaInit:
    def test_skipped_when_a_plugin_already_requires_postgres(self) -> None:
        """The eager, fatal path owns it — the fallback must not run twice."""
        assert (
            should_run_core_schema_init({"postgres", "redis"}, postgres_enabled=True)
            is False
        )

    def test_runs_when_postgres_is_enabled_but_only_optional(self) -> None:
        """The regression this exists for."""
        assert should_run_core_schema_init(set(), postgres_enabled=True) is True
        assert should_run_core_schema_init({"redis"}, postgres_enabled=True) is True

    def test_skipped_when_postgres_is_disabled(self) -> None:
        assert should_run_core_schema_init(set(), postgres_enabled=False) is False


class TestInitCoreSchemaBestEffort:
    @pytest.mark.asyncio
    async def test_reports_success_when_the_upgrade_runs(self) -> None:
        with patch("core.db.schema.init_db", new=AsyncMock()) as init_db:
            assert await init_core_schema_best_effort() is True
        init_db.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_an_unreachable_database_does_not_break_boot(self) -> None:
        """Postgres enabled but down was survivable before; it must stay so.

        Making the fallback fatal would turn "no plugin needs Postgres and the
        database happens to be down" from a degraded boot into a crash loop.
        """
        with patch(
            "core.db.schema.init_db",
            new=AsyncMock(side_effect=OSError("connection refused")),
        ):
            assert await init_core_schema_best_effort() is False

    @pytest.mark.asyncio
    async def test_the_failure_is_logged_not_swallowed(self) -> None:
        with (
            patch(
                "core.db.schema.init_db",
                new=AsyncMock(side_effect=OSError("connection refused")),
            ),
            patch("core.db.schema.logger") as logger,
        ):
            await init_core_schema_best_effort()

        assert logger.error.called, "a skipped core schema init must leave a trace"
        message = logger.error.call_args.args[0]
        assert "core_schema_init_failed" in message
