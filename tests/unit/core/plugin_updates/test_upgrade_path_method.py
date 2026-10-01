"""Upgrade path stops and installation-method detection."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.config.plugin_updates import PluginUpdateConfig
from core.plugin_updates.models import ReleaseInfo
from core.plugin_updates.upgrade import detect_install_method, upgrade_path


def _rel(version: str) -> ReleaseInfo:
    return ReleaseInfo(
        plugin="core", version=version, tag=f"v{version}", published_at=None
    )


def _releases(*versions: str) -> list[ReleaseInfo]:
    return [_rel(v) for v in versions]


def test_direct_upgrade_within_a_major_has_no_stops() -> None:
    assert upgrade_path("1.2.0", _releases("1.5.0", "1.4.0", "1.2.0")) == []


def test_crossing_majors_stops_at_the_latest_of_each_major() -> None:
    rels = _releases("3.1.0", "3.0.0", "2.3.0", "2.0.0", "1.5.0", "1.4.0", "1.2.0")
    assert upgrade_path("1.2.0", rels) == ["1.5.0", "2.3.0", "3.1.0"]


def test_no_stop_in_the_current_major_when_already_on_its_latest() -> None:
    assert upgrade_path("1.5.0", _releases("2.1.0", "2.0.0", "1.5.0")) == ["2.1.0"]


def test_a_major_without_releases_is_skipped() -> None:
    assert upgrade_path("1.0.0", _releases("3.0.0", "1.1.0")) == ["1.1.0", "3.0.0"]


def test_nothing_newer_means_no_path() -> None:
    assert upgrade_path("2.0.0", _releases("1.9.0")) == []


def test_unparseable_installed_version_means_no_path() -> None:
    assert upgrade_path("not-a-version", _releases("2.0.0")) == []


def _cfg(**kw: object) -> PluginUpdateConfig:
    return PluginUpdateConfig(**kw)  # type: ignore[arg-type]


def test_configured_method_wins(tmp_path: Path) -> None:
    cfg = _cfg(install_method="docker")
    env = {"KUBERNETES_SERVICE_HOST": "10.0.0.1"}
    assert detect_install_method(cfg, environ=env, root=tmp_path) == ("docker", False)


def test_method_setting_is_case_insensitive_and_rejects_unknown_values() -> None:
    assert _cfg(install_method=" Helm ").install_method == "helm"
    assert _cfg(install_method="ansible").install_method is None


def test_method_reads_the_documented_environment_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SYSTEM_INSTALL_METHOD", "pip")
    assert PluginUpdateConfig().install_method == "pip"


def test_kubernetes_is_detected_as_helm(tmp_path: Path) -> None:
    env = {"KUBERNETES_SERVICE_HOST": "10.0.0.1"}
    assert detect_install_method(_cfg(), environ=env, root=tmp_path) == ("helm", True)


def test_dockerenv_is_detected_as_docker(tmp_path: Path) -> None:
    (tmp_path / ".dockerenv").write_text("")
    assert detect_install_method(_cfg(), environ={}, root=tmp_path) == (
        "docker",
        True,
    )


def test_container_cgroup_is_detected_as_docker(tmp_path: Path) -> None:
    cgroup = tmp_path / "proc" / "1" / "cgroup"
    cgroup.parent.mkdir(parents=True)
    cgroup.write_text("0::/system.slice/containerd.service/docker-abc.scope\n")
    assert detect_install_method(_cfg(), environ={}, root=tmp_path) == (
        "docker",
        True,
    )


def test_a_plain_host_is_pip(tmp_path: Path) -> None:
    core = tmp_path / "site-packages" / "core"
    got = detect_install_method(_cfg(), environ={}, root=tmp_path, package_dir=core)
    assert got == ("pip", True)


def test_an_instructions_file_without_a_method_means_custom(tmp_path: Path) -> None:
    cfg = _cfg(upgrade_instructions_file=tmp_path / "upgrade.md")
    env = {"KUBERNETES_SERVICE_HOST": "10.0.0.1"}
    assert detect_install_method(cfg, environ=env, root=tmp_path) == ("custom", False)
