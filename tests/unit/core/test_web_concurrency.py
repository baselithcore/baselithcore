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


class TestSharedCpuThreads:
    """N workers must not each size their math thread pool to the machine."""

    @pytest.fixture(autouse=True)
    def _clean_threads(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from core.config.concurrency import THREAD_POOL_ENV_VARS

        for name in (*THREAD_POOL_ENV_VARS, "WEB_CONCURRENCY"):
            monkeypatch.delenv(name, raising=False)

    def test_workers_split_the_cpus(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import os

        import core.config.concurrency as concurrency

        monkeypatch.setattr(concurrency, "_available_cpus", lambda: 8)
        set_web_concurrency(4)
        assert concurrency.share_cpu_threads() == 2
        assert os.environ["OMP_NUM_THREADS"] == "2"
        assert os.environ["MKL_NUM_THREADS"] == "2"

    def test_the_container_worker_count_counts_too(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import core.config.concurrency as concurrency

        monkeypatch.setattr(concurrency, "_available_cpus", lambda: 8)
        monkeypatch.setenv("WEB_CONCURRENCY", "3")
        assert concurrency.share_cpu_threads() == 2

    def test_never_below_one_thread(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import core.config.concurrency as concurrency

        monkeypatch.setattr(concurrency, "_available_cpus", lambda: 2)
        set_web_concurrency(8)
        assert concurrency.share_cpu_threads() == 1

    def test_an_operator_setting_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import os

        import core.config.concurrency as concurrency

        monkeypatch.setattr(concurrency, "_available_cpus", lambda: 8)
        monkeypatch.setenv("OMP_NUM_THREADS", "6")
        set_web_concurrency(4)
        concurrency.share_cpu_threads()
        assert os.environ["OMP_NUM_THREADS"] == "6"

    def test_a_single_process_is_left_alone(self) -> None:
        import os

        import core.config.concurrency as concurrency

        assert concurrency.share_cpu_threads() is None
        assert "OMP_NUM_THREADS" not in os.environ
