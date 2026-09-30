"""Upgrade instructions: distributions, compose files, source checkouts, robustness."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from core._core_version import CORE_VERSION
from core.config.plugin_updates import PluginUpdateConfig
from core.plugin_updates import service as svc_mod
from core.plugin_updates.upgrade import (
    Deployment,
    detect_install_method,
    render_instructions,
)
from core.plugin_updates.upgrade.method import is_source_checkout

from .test_upgrade_instructions import _build, _commands
from .test_upgrade_service import _cfg as _svc_cfg
from .test_upgrade_service import _report

# -- C1: a downstream distribution is never told the public procedure --------


@pytest.mark.parametrize("method", ["helm", "docker", "pip", "source"])
def test_a_distribution_gets_no_public_core_steps(method: str) -> None:
    instr = _build(method, distribution="acme")
    assert instr.distribution == "acme"
    assert [s.id for s in instr.steps] == ["distribution.procedure"]
    step = instr.steps[0]
    assert step.command is None
    assert "acme" in step.text
    assert "SYSTEM_UPGRADE_INSTRUCTIONS_FILE" in step.text
    assert "SYSTEM_UPGRADE_GUIDE_URL" in step.text
    joined = " ".join(_commands(instr))
    assert "baselith-core==" not in joined and "ghcr.io" not in joined
    ids = [c.id for c in instr.checklist]
    assert ids[0].startswith("checklist.backup")
    assert "checklist.release_notes" in ids and "checklist.health" in ids


def test_a_distribution_with_its_own_procedure_shows_it(tmp_path: Path) -> None:
    doc = tmp_path / "upgrade.md"
    doc.write_text("run make deploy V={version}")
    cfg = PluginUpdateConfig(upgrade_instructions_file=doc)
    instr = _build("custom", config=cfg, distribution="acme")
    assert instr.steps == []
    assert instr.custom_text == "run make deploy V=0.41.0"


def test_the_public_core_has_no_distribution() -> None:
    instr = _build("pip")
    assert instr.distribution is None
    assert instr.steps[0].id == "pip.install"


# -- I1: docker names the compose file and separates the two alternatives ---


def test_docker_steps_name_the_compose_file_and_offer_two_alternatives() -> None:
    instr = _build("docker")
    ids = [s.id for s in instr.steps]
    assert ids == ["docker.build_from_tag", "docker.prebuilt_image", "docker.migrate"]
    build, image, migrate = instr.steps
    assert build.command == (
        "git fetch --tags && git checkout v0.41.0 && "
        "docker compose -f compose.prod.yaml build && "
        "docker compose -f compose.prod.yaml up -d"
    )
    assert "compose.prod.yaml" in build.text
    assert "image: ghcr.io/baselithcore/baselithcore:0.41.0" in image.text
    assert instr.image == "ghcr.io/baselithcore/baselithcore:0.41.0"
    assert _build("pip").image is None
    assert image.command == (
        "docker compose -f compose.prod.yaml pull api worker && "
        "docker compose -f compose.prod.yaml up -d"
    )
    assert migrate.command == (
        "docker compose -f compose.prod.yaml exec api baselith db migrate"
    )
    for cmd in _commands(instr):
        assert "docker compose " not in cmd.replace(
            "docker compose -f compose.prod.yaml", ""
        )


def test_docker_backup_states_what_the_script_assumes() -> None:
    backup = _build("docker").checklist[0]
    assert backup.id == "checklist.backup.docker"
    assert "compose.prod.yaml" in backup.text and "root" in backup.text
    assert backup.command == "./scripts/backup-db.sh"


# -- I2: a source checkout is not a pip installation -------------------------


def test_source_steps_check_out_the_tag_and_reinstall() -> None:
    instr = _build("source")
    assert _commands(instr) == [
        "git fetch --tags && git checkout v0.41.0",
        "pip install -e .",
        "baselith db migrate",
    ]
    assert instr.steps[-1].id == "source.restart"
    assert instr.checklist[0].id == "checklist.backup.source"
    assert "pg_dump" in (instr.checklist[0].command or "")


def test_a_package_outside_site_packages_is_a_source_checkout(tmp_path: Path) -> None:
    core = tmp_path / "checkout" / "core"
    core.mkdir(parents=True)
    assert is_source_checkout(core)
    got = detect_install_method(
        PluginUpdateConfig(), environ={}, root=tmp_path, package_dir=core
    )
    assert got == ("source", True)


def test_a_git_directory_next_to_core_is_a_source_checkout(tmp_path: Path) -> None:
    core = tmp_path / "site-packages" / "core"
    core.mkdir(parents=True)
    assert not is_source_checkout(core)
    (tmp_path / "site-packages" / ".git").mkdir()
    assert is_source_checkout(core)


def test_an_installed_wheel_is_pip(tmp_path: Path) -> None:
    core = tmp_path / "lib" / "python3.12" / "site-packages" / "core"
    core.mkdir(parents=True)
    got = detect_install_method(
        PluginUpdateConfig(), environ={}, root=tmp_path, package_dir=core
    )
    assert got == ("pip", True)


def test_source_is_a_valid_configured_method() -> None:
    assert PluginUpdateConfig(install_method="Source").install_method == "source"  # type: ignore[call-arg]


# -- M2: podman ---------------------------------------------------------------


def test_a_podman_container_is_docker(tmp_path: Path) -> None:
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / ".containerenv").write_text("")
    got = detect_install_method(
        PluginUpdateConfig(), environ={}, root=tmp_path, package_dir=tmp_path
    )
    assert got == ("docker", True)


# -- M3: a valid Kubernetes Job name -------------------------------------------


def test_helm_backup_job_name_is_a_valid_kubernetes_name() -> None:
    cmd = _build("helm").checklist[0].command or ""
    assert cmd.endswith(" baselithcore-before-0-41-0")


# -- M1: a blank instructions file is no instructions file --------------------


def test_a_blank_instructions_file_setting_is_unset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SYSTEM_UPGRADE_INSTRUCTIONS_FILE", "  ")
    cfg = PluginUpdateConfig()
    assert cfg.upgrade_instructions_file is None
    got = detect_install_method(cfg, environ={}, root=tmp_path, package_dir=tmp_path)
    assert got[0] != "custom"


# -- M5: only a regular file is read, and a read is reused -------------------


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs FIFOs")
def test_a_fifo_is_refused_without_blocking(tmp_path: Path) -> None:
    fifo = tmp_path / "upgrade.md"
    os.mkfifo(fifo)
    assert render_instructions(fifo, version="1", current="0") == (
        None,
        "the upgrade instructions file is not a regular file",
    )


def test_a_directory_is_refused(tmp_path: Path) -> None:
    text, error = render_instructions(tmp_path, version="1", current="0")
    assert (
        text is None and error == "the upgrade instructions file is not a regular file"
    )


def test_the_file_is_read_again_only_when_it_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core.plugin_updates.upgrade import custom

    doc = tmp_path / "u.md"
    doc.write_text("v1 {version}")
    reads: list[int] = []
    real = custom._read_regular_file

    def counting(path: Path) -> bytes:
        reads.append(1)
        return real(path)

    monkeypatch.setattr(custom, "_read_regular_file", counting)
    assert render_instructions(doc, version="2", current="1")[0] == "v1 2"
    assert render_instructions(doc, version="3", current="1")[0] == "v1 3"
    assert len(reads) == 1
    doc.write_text("v2 changed {version}")
    os.utime(doc, ns=(1, 10**18))
    assert render_instructions(doc, version="3", current="1")[0] == "v2 changed 3"
    assert len(reads) == 2


# -- I3: a failure while presenting never breaks the report -------------------


def test_a_failing_plugin_scan_reports_the_check_as_not_computed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(_root: Path) -> list[object]:
        raise RuntimeError("bad overlay")

    monkeypatch.setattr(svc_mod, "installed_bounds", boom)
    svc = svc_mod.PluginUpdateService(_svc_cfg(tmp_path), bundled_root=tmp_path)
    svc._deployment = Deployment(
        method="pip",
        detected=False,
        framework_version=CORE_VERSION,
        distribution=None,
    )
    svc._cache.save(_report())
    served = svc.report()
    assert served is not None and served.system is not None
    upgrade = served.system.upgrade
    assert upgrade is not None and upgrade.plugins.checked is False
    assert upgrade.plugins.reason is not None and "read" in upgrade.plugins.reason


def test_a_failing_instructions_build_serves_the_notice_without_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("template bug")

    monkeypatch.setattr(svc_mod, "build_upgrade_instructions", boom)
    svc = svc_mod.PluginUpdateService(_svc_cfg(tmp_path), bundled_root=tmp_path)
    svc._cache.save(_report())
    with caplog.at_level("WARNING"):
        first = svc.report()
        second = svc.report()
    for served in (first, second):
        assert served is not None and served.system is not None
        assert served.system.available and served.system.upgrade is None
        assert served.candidates[0].install is not None
    warnings = [r for r in caplog.records if "upgrade_instructions_failed" in r.message]
    assert len(warnings) == 1


def test_failing_plugin_guidance_serves_candidates_without_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("guidance bug")

    monkeypatch.setattr(svc_mod, "plugin_install_guidance", boom)
    svc = svc_mod.PluginUpdateService(_svc_cfg(tmp_path), bundled_root=tmp_path)
    svc._cache.save(_report())
    served = svc.report()
    assert served is not None and served.candidates[0].install is None
    assert served.system is not None and served.system.upgrade is not None


async def test_check_now_survives_a_failing_presentation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_system(*a: object, **k: object) -> object:
        return _report().system

    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("template bug")

    monkeypatch.setattr(svc_mod, "check_system", fake_system)
    monkeypatch.setattr(svc_mod, "build_upgrade_instructions", boom)
    svc = svc_mod.PluginUpdateService(_svc_cfg(tmp_path), bundled_root=tmp_path)
    served = await svc.check_now()
    assert served.system is not None and served.system.upgrade is None
    assert svc._cache.load() is not None
