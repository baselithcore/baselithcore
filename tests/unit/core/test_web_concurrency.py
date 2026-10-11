"""The worker-count seam plugins use to detect multi-process deployments."""

from __future__ import annotations

import pytest

from baselith.core.config.concurrency import (
    WEB_CONCURRENCY_ENV,
    get_web_concurrency,
    set_web_concurrency,
)


def _owned_and_unset(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """Unset *name* for this test and restore it afterwards.

    ``delenv(..., raising=False)`` on an unset variable records nothing, so a
    value the code under test writes into ``os.environ`` outlived the test (a
    leaked BASELITH_WEB_CONCURRENCY=4 failed an unrelated boot-report test).
    Setting it first makes monkeypatch own the variable.
    """
    monkeypatch.setenv(name, "")
    monkeypatch.delenv(name)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _owned_and_unset(monkeypatch, WEB_CONCURRENCY_ENV)


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


class TestSharedCpuThreads:
    """N workers must not each size their math thread pool to the machine."""

    @pytest.fixture(autouse=True)
    def _clean_threads(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from baselith.core.config.concurrency import THREAD_POOL_ENV_VARS

        for name in (*THREAD_POOL_ENV_VARS, "WEB_CONCURRENCY"):
            _owned_and_unset(monkeypatch, name)

    def test_workers_split_the_cpus(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import os

        import baselith.core.config.concurrency as concurrency

        monkeypatch.setattr(concurrency, "_available_cpus", lambda: 8)
        set_web_concurrency(4)
        assert concurrency.share_cpu_threads() == 2
        assert os.environ["OMP_NUM_THREADS"] == "2"
        assert os.environ["MKL_NUM_THREADS"] == "2"

    def test_the_container_worker_count_counts_too(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import baselith.core.config.concurrency as concurrency

        monkeypatch.setattr(concurrency, "_available_cpus", lambda: 8)
        monkeypatch.setenv("WEB_CONCURRENCY", "3")
        assert concurrency.share_cpu_threads() == 2

    def test_never_below_one_thread(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import baselith.core.config.concurrency as concurrency

        monkeypatch.setattr(concurrency, "_available_cpus", lambda: 2)
        set_web_concurrency(8)
        assert concurrency.share_cpu_threads() == 1

    def test_an_operator_setting_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import os

        import baselith.core.config.concurrency as concurrency

        monkeypatch.setattr(concurrency, "_available_cpus", lambda: 8)
        monkeypatch.setenv("OMP_NUM_THREADS", "6")
        set_web_concurrency(4)
        concurrency.share_cpu_threads()
        assert os.environ["OMP_NUM_THREADS"] == "6"

    def test_a_single_process_is_left_alone(self) -> None:
        import os

        import baselith.core.config.concurrency as concurrency

        assert concurrency.share_cpu_threads() is None
        assert "OMP_NUM_THREADS" not in os.environ
