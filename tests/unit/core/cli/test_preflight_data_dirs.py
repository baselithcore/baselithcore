"""Startup must not die over a directory the CLI would create itself.

`check_data_dirs` requires `$CORE_DATA_DIR`, plus its `catalog` and
`compliance` subdirectories. `_run_preflight` treats every non-connectivity
failure as fatal, so on a host where one of them was never created
`baselith run` exits 1 — and under `Restart=always` that is an endless crash
loop whose only remedy is `mkdir`. That is exactly what took the deploy host
down for seventeen hours after the directories check first shipped.

The diagnostic itself stays read-only: `baselith doctor` reports, it does not
mutate the filesystem. Only the server's own startup path repairs what it can,
and a directory that genuinely cannot be created still stops the boot.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.cli.commands.doctor_checks import check_data_dirs, ensure_data_dirs


@pytest.fixture(autouse=True)
def _isolated_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point CORE_DATA_DIR at an empty tmp tree, away from the repo's own."""
    monkeypatch.chdir(tmp_path)
    data_dir = tmp_path / "data"
    monkeypatch.setenv("CORE_DATA_DIR", str(data_dir))
    return data_dir


def test_ensure_creates_every_missing_directory(_isolated_data_dir: Path) -> None:
    created = ensure_data_dirs()

    assert sorted(path.name for path in created) == ["catalog", "compliance", "data"]
    assert check_data_dirs().passed


def test_ensure_reports_only_what_it_created(_isolated_data_dir: Path) -> None:
    _isolated_data_dir.mkdir()
    (_isolated_data_dir / "compliance").mkdir()

    created = ensure_data_dirs()

    assert [path.name for path in created] == ["catalog"]


def test_ensure_is_idempotent(_isolated_data_dir: Path) -> None:
    ensure_data_dirs()

    assert ensure_data_dirs() == []
    assert check_data_dirs().passed


def test_the_diagnostic_alone_creates_nothing(_isolated_data_dir: Path) -> None:
    result = check_data_dirs()

    assert not result.passed
    assert not _isolated_data_dir.exists()


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root writes through a read-only directory",
)
def test_a_directory_that_cannot_be_created_still_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    readonly = tmp_path / "readonly"
    readonly.mkdir(mode=0o500)
    monkeypatch.setenv("CORE_DATA_DIR", str(readonly / "data"))

    created = ensure_data_dirs()

    assert created == []
    assert not check_data_dirs().passed


def test_startup_preflight_repairs_before_it_judges(
    monkeypatch: pytest.MonkeyPatch, _isolated_data_dir: Path
) -> None:
    """The wiring, not just the helper: a missing dir must not exit non-zero."""
    import core.cli.commands.doctor as doctor
    from core.cli.commands.run import _run_preflight

    monkeypatch.setattr(
        doctor, "run_checks", lambda **_kwargs: [check_data_dirs()], raising=True
    )

    assert _run_preflight() == 0
    assert (_isolated_data_dir / "catalog").is_dir()
