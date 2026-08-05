"""The worker-count seam plugins use to detect multi-process deployments."""

from __future__ import annotations

import pytest

from core.config.concurrency import (
    WEB_CONCURRENCY_ENV,
    get_web_concurrency,
    set_web_concurrency,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(WEB_CONCURRENCY_ENV, raising=False)


def test_unset_means_single_process() -> None:
    assert get_web_concurrency() == 1


def test_the_launcher_records_the_worker_count() -> None:
    set_web_concurrency(4)
    assert get_web_concurrency() == 4


def test_a_single_worker_clears_a_stale_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    """A value inherited from an outer environment must not outlive a
    single-process run, or a plugin refuses a backend it could have used."""
    monkeypatch.setenv(WEB_CONCURRENCY_ENV, "8")

    set_web_concurrency(1)

    assert get_web_concurrency() == 1


def test_a_malformed_value_is_read_as_single_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never raise here: this is called on the boot path."""
    monkeypatch.setenv(WEB_CONCURRENCY_ENV, "lots")

    assert get_web_concurrency() == 1
