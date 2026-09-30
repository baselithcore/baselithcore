"""``PLUGIN_UPDATE_TRUST``: the setting and how the service applies it."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

import core.plugin_updates.service as svc_mod
from core.config.plugin_updates import PluginUpdateConfig
from core.plugin_updates.models import CheckReport, UpdateCandidate


def test_provenance_is_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PLUGIN_UPDATE_TRUST", raising=False)
    assert PluginUpdateConfig().trust == "provenance"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("signed", "signed"),
        (" Signed ", "signed"),
        ("provenance", "provenance"),
        ("", "provenance"),
        ("sigend", "signed"),  # a typo must never loosen the rule
        ("none", "signed"),
    ],
)
def test_env_value_is_normalised_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: str
) -> None:
    monkeypatch.setenv("PLUGIN_UPDATE_TRUST", raw)
    assert PluginUpdateConfig().trust == expected


def _cfg(tmp: Path, trust: str) -> PluginUpdateConfig:
    src = tmp / "m.yaml"
    src.write_text("mirrors:\n  demo:\n    repo: git@github.com:o/r.git\n")
    return PluginUpdateConfig(
        sources_file=src,
        cache_dir=tmp / "c",
        core_update_repo="",
        trust=trust,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize("trust", ["provenance", "signed"])
async def test_service_passes_the_mode_and_reads_keys_only_when_signed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, trust: str
) -> None:
    seen: dict[str, object] = {}
    loaded: list[bool] = []

    async def fake(*_: object, **kw: object) -> CheckReport:
        seen.update(kw)
        return CheckReport(checked_at=datetime.now(UTC), candidates=[])

    def keys() -> list[object]:
        loaded.append(True)
        return []

    monkeypatch.setattr(svc_mod, "run_check", fake)
    monkeypatch.setattr(svc_mod, "load_trusted_keys", keys)
    svc = svc_mod.PluginUpdateService(_cfg(tmp_path, trust), bundled_root=tmp_path)
    await svc.check_now()
    assert seen["trust"] == trust
    assert bool(loaded) is (trust == "signed")


def _saved(trust: str | None) -> CheckReport:
    return CheckReport(
        checked_at=datetime.now(UTC),
        candidates=[
            UpdateCandidate(
                plugin="demo",
                installed_version="1.0.0",
                latest=None,
                available=True,
                trust=trust,  # type: ignore[arg-type]
            )
        ],
    )


@pytest.mark.parametrize(
    ("mode", "saved", "kept"),
    [
        ("provenance", "provenance", True),
        ("provenance", "signed", False),
        ("signed", "provenance", False),
        ("signed", "signed", True),
        ("provenance", None, False),
    ],
)
async def test_a_verdict_from_another_mode_is_never_served_or_carried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    saved: str | None,
    kept: bool,
) -> None:
    """Switching PLUGIN_UPDATE_TRUST must not keep serving the old mode's offers."""
    svc = svc_mod.PluginUpdateService(_cfg(tmp_path, mode), bundled_root=tmp_path)
    svc._cache.save(_saved(saved))
    served = svc.report()
    assert served is not None and bool(served.candidates) is kept

    async def boom(*_: object, **__: object) -> CheckReport:
        raise RuntimeError("github down")

    monkeypatch.setattr(svc_mod, "run_check", boom)
    report = await svc.check_now()
    # A failed check carries only the current mode's verdicts, and saves only those.
    assert bool(report.candidates) is kept and report.error == "RuntimeError"
    reloaded = svc._cache.load()
    assert reloaded is not None and bool(reloaded.candidates) is kept
