"""Misspelled environment variables must be reported, not silently ignored.

Every settings class declares ``extra="ignore"``, so ``CORE_LOG_LEVL=DEBUG`` is
accepted and dropped: the operator sees the default and no error explaining why.
``core.config.drift`` reports names that closely resemble a declared setting
without matching one, and — deliberately — nothing else, so the warning stays
worth reading.
"""

import logging
import os

import core.config  # noqa: F401  — binds every settings class for the name table
from core.config.drift import (
    EnvSuspect,
    known_setting_names,
    suspected_typos,
    warn_on_suspected_typos,
)


class TestKnownSettingNames:
    """The name table is derived from the loaded settings classes."""

    def test_collects_declared_and_prefixed_names(self):
        names = known_setting_names()

        assert "CORE_LOG_LEVEL" in names  # env_prefix + field
        assert "SECRET_KEY" in names  # explicit alias
        assert "VISION_ANTHROPIC_API_KEY" in names  # AliasChoices, prefixed
        assert "ANTHROPIC_API_KEY" in names  # AliasChoices, bare fallback


class TestSuspectedTypos:
    """A near-miss is reported; anything else is left alone."""

    def test_reports_a_near_miss_with_its_closest_setting(self):
        suspects = suspected_typos({"CORE_LOG_LEVL": "DEBUG"})

        assert suspects == [
            EnvSuspect(name="CORE_LOG_LEVL", suggestion="CORE_LOG_LEVEL")
        ]

    def test_exact_names_are_not_suspects(self):
        assert suspected_typos({"CORE_LOG_LEVEL": "DEBUG"}) == []

    def test_unrelated_names_are_not_suspects(self):
        """Most of a process's environment has nothing to do with the app."""
        environ = {"PATH": "/usr/bin", "HOME": "/root", "TZ": "UTC"}

        assert suspected_typos(environ) == []

    def test_runtime_families_are_skipped(self):
        """``BASELITH_FLAG_<FLAG>`` picks its suffix at runtime, so no declared
        name exists to compare it against."""
        environ = {
            "BASELITH_FLAG_NEW_ROUTER": "true",
            "BASELITH_PROMPT_VARIANTS_REACT_SYSTEM": "1:50,2:50",
        }

        assert suspected_typos(environ) == []

    def test_distinct_settings_are_not_confused_for_each_other(self):
        """Two real settings that merely share a prefix must not be paired."""
        assert suspected_typos({"CORE_DATA_DIR": "/data"}) == []

    def test_cutoff_is_configurable(self):
        loose = suspected_typos({"CORE_LOG": "DEBUG"}, cutoff=0.5)

        assert [suspect.name for suspect in loose] == ["CORE_LOG"]

    def test_suspect_renders_an_actionable_message(self):
        message = str(EnvSuspect(name="JWT_ALGORITM", suggestion="JWT_ALGORITHM"))

        assert "JWT_ALGORITM" in message and "JWT_ALGORITHM" in message


class TestStartupWarning:
    """Startup reporting warns and never raises."""

    def test_warns_once_per_suspect(self, monkeypatch, caplog):
        monkeypatch.setenv("CORE_LOG_LEVL", "DEBUG")

        with caplog.at_level(logging.WARNING, logger="core.config.drift"):
            suspects = warn_on_suspected_typos()

        assert [suspect.name for suspect in suspects] == ["CORE_LOG_LEVL"]
        assert any("CORE_LOG_LEVL" in record.message for record in caplog.records)

    def test_clean_environment_logs_nothing(self, monkeypatch, caplog):
        """Cleared rather than trusted: the ambient environment is not hermetic."""
        monkeypatch.setattr(os, "environ", {"PATH": "/usr/bin"})

        with caplog.at_level(logging.WARNING, logger="core.config.drift"):
            assert warn_on_suspected_typos() == []

        assert not [r for r in caplog.records if "matches no setting" in r.message]
