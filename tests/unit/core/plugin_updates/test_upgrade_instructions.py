"""Version-specific upgrade instructions: templates, checklist, custom file."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.config.plugin_updates import PluginUpdateConfig
from core.plugin_updates.models import ReleaseInfo, SystemUpdate
from core.plugin_updates.upgrade import (
    MAX_INSTRUCTIONS_BYTES,
    Deployment,
    build_upgrade_instructions,
    installed_bounds,
    plugin_compatibility,
    plugin_install_guidance,
    read_namespace,
    render_instructions,
)
from core.plugin_updates.upgrade_models import PluginBoundsIssue, UpgradeInstructions

REPO = "baselithcore/baselithcore"


def _system(latest: str = "0.41.0", path: list[str] | None = None) -> SystemUpdate:
    return SystemUpdate(
        repo=REPO,
        installed_version="0.40.2",
        latest=ReleaseInfo(
            plugin="core",
            version=latest,
            tag=f"v{latest}",
            published_at=None,
            html_url=f"https://github.com/{REPO}/releases/tag/v{latest}",
        ),
        available=True,
        behind=2,
        upgrade_path=path or [],
    )


def _deploy(method: str = "pip", **kw: object) -> Deployment:
    base: dict[str, object] = {
        "method": method,
        "detected": True,
        "framework_version": "0.40.2",
    }
    base.update(kw)
    return Deployment(**base)  # type: ignore[arg-type]


def _build(
    method: str = "pip",
    system: SystemUpdate | None = None,
    config: PluginUpdateConfig | None = None,
    bounds: list[PluginBoundsIssue] | None = None,
    **kw: object,
) -> UpgradeInstructions:
    result = build_upgrade_instructions(
        system or _system(),
        config or PluginUpdateConfig(),
        _deploy(method, **kw),
        bounds or [],
    )
    assert result is not None
    return result


def _commands(instr: UpgradeInstructions) -> list[str]:
    return [s.command for s in instr.steps if s.command]


def test_nothing_newer_means_no_instructions() -> None:
    system = SystemUpdate(repo=REPO, installed_version="0.40.2")
    assert (
        build_upgrade_instructions(system, PluginUpdateConfig(), _deploy(), []) is None
    )


def test_pip_steps_install_the_exact_release_then_migrate() -> None:
    instr = _build("pip")
    assert instr.method == "pip" and instr.target_version == "0.41.0"
    assert _commands(instr) == [
        'pip install --upgrade "baselith-core==0.41.0"',
        "baselith db migrate",
    ]
    assert instr.steps[-1].id == "pip.restart" and instr.steps[-1].command is None


def test_helm_steps_use_the_chart_at_the_release_tag() -> None:
    instr = _build("helm", namespace="prod-ns")
    cmds = _commands(instr)
    assert cmds[1] == (
        "git clone --depth 1 --branch v0.41.0 "
        "https://github.com/baselithcore/baselithcore.git baselithcore-0.41.0"
    )
    assert cmds[2] == (
        "helm upgrade <release> ./baselithcore-0.41.0/deploy/helm/baselithcore "
        "--namespace prod-ns --reset-then-reuse-values --set image.tag=0.41.0 "
        "--wait --timeout 15m"
    )
    assert cmds[3] == "helm test <release> --namespace prod-ns"
    backup = instr.checklist[0]
    assert (
        backup.command is not None
        and "--from=cronjob/<fullname>-backup" in backup.command
    )


def test_helm_without_a_known_namespace_leaves_a_placeholder() -> None:
    assert "--namespace <namespace>" in _commands(_build("helm"))[2]


def test_checklist_backup_notes_plugins_and_post_checks() -> None:
    instr = _build("pip", base_url="https://ai.example.com/")
    ids = [s.id for s in instr.checklist]
    assert ids == [
        "checklist.backup.pip",
        "checklist.release_notes",
        "checklist.plugins",
        "checklist.health",
        "checklist.console_version",
    ]
    assert instr.checklist[1].url == f"https://github.com/{REPO}/releases"
    assert instr.checklist[3].command == "curl -fsS https://ai.example.com/health/ready"
    assert instr.release_notes_url == f"https://github.com/{REPO}/releases/tag/v0.41.0"


def test_a_major_jump_installs_the_first_stop_first() -> None:
    instr = _build("pip", system=_system("2.1.0", ["0.48.0", "1.9.0", "2.1.0"]))
    assert instr.path == ["0.48.0", "1.9.0", "2.1.0"]
    assert instr.target_version == "0.48.0" and instr.latest_version == "2.1.0"
    assert "baselith-core==0.48.0" in _commands(instr)[0]


def test_guide_url_travels_with_the_instructions() -> None:
    cfg = PluginUpdateConfig(upgrade_guide_url="https://ops.example.com/upgrade")
    assert _build(config=cfg).guide_url == "https://ops.example.com/upgrade"


def test_custom_method_renders_the_operator_file(tmp_path: Path) -> None:
    doc = tmp_path / "upgrade.md"
    doc.write_text(
        "# Upgrade\n\nFrom {current} to {version}: `make deploy V={version}`\n"
    )
    cfg = PluginUpdateConfig(upgrade_instructions_file=doc)
    instr = _build("custom", config=cfg)
    assert instr.steps == []
    assert instr.custom_text == (
        "# Upgrade\n\nFrom 0.40.2 to 0.41.0: `make deploy V=0.41.0`\n"
    )
    assert instr.custom_error is None
    assert (
        instr.checklist[0].id == "checklist.backup"
        and instr.checklist[0].command is None
    )


def test_custom_method_reports_a_missing_file(tmp_path: Path) -> None:
    cfg = PluginUpdateConfig(upgrade_instructions_file=tmp_path / "nope.md")
    instr = _build("custom", config=cfg)
    assert instr.custom_text is None
    assert instr.custom_error == "the upgrade instructions file cannot be read"


def test_render_leaves_other_braces_alone(tmp_path: Path) -> None:
    doc = tmp_path / "u.md"
    doc.write_text("{version} {0} {current!r} {{version}}")
    text, err = render_instructions(doc, version="1.0.0", current="0.9.0")
    assert err is None and text == "1.0.0 {0} {current!r} {1.0.0}"


def test_render_refuses_an_oversized_file(tmp_path: Path) -> None:
    doc = tmp_path / "u.md"
    doc.write_bytes(b"x" * (MAX_INSTRUCTIONS_BYTES + 1))
    assert render_instructions(doc, version="1", current="0") == (
        None,
        "the upgrade instructions file is larger than 64 KiB",
    )


def test_render_without_a_file_is_empty() -> None:
    assert render_instructions(None, version="1", current="0") == (None, None)


def _bounds(name: str, low: str | None, high: str | None) -> PluginBoundsIssue:
    return PluginBoundsIssue(
        plugin=name, version="1.0.0", min_core_version=low, max_core_version=high
    )


def test_plugins_whose_bounds_exclude_the_target_are_listed() -> None:
    bounds = [
        _bounds("ok", "0.30.0", None),
        _bounds("too_old", None, "0.40.9"),
        _bounds("too_new", "0.50.0", None),
    ]
    compat = plugin_compatibility(
        "0.41.0",
        bounds,
        framework_version="0.40.2",
        core_version="0.40.2",
        distribution=None,
    )
    assert compat.checked
    assert [b.plugin for b in compat.incompatible] == ["too_old", "too_new"]


def test_a_distribution_does_not_compare_bounds_against_the_public_core() -> None:
    compat = plugin_compatibility(
        "0.41.0",
        [_bounds("x", "1.0.0", None)],
        framework_version="1.14.0",
        core_version="0.40.2",
        distribution="acme",
    )
    assert not compat.checked and compat.incompatible == []
    assert compat.reason is not None and "acme" in compat.reason


def test_installed_bounds_reads_manifests(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "manifest.yaml").write_text(
        "name: a\nversion: 1.2.0\nmin_core_version: 0.30.0\n"
    )
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "manifest.yaml").write_text("name: b\nversion: 1.0.0\n")
    (tmp_path / "c").mkdir()
    (tmp_path / "c" / "manifest.yaml").write_text(": not yaml : [")
    found = installed_bounds(tmp_path)
    assert [(b.plugin, b.min_core_version) for b in found] == [("a", "0.30.0")]


def test_installed_bounds_of_a_missing_root_is_empty(tmp_path: Path) -> None:
    assert installed_bounds(tmp_path / "missing") == []


def test_plugin_guidance_is_never_automated() -> None:
    cfg = PluginUpdateConfig(upgrade_guide_url="https://ops.example.com/p")
    guidance = plugin_install_guidance(cfg, "helm")
    assert guidance.automated is False
    assert (
        guidance.method == "helm" and guidance.guide_url == "https://ops.example.com/p"
    )


@pytest.mark.parametrize(
    ("content", "expected"),
    [("prod-ns\n", "prod-ns"), ("", None), ("bad ns;rm", None)],
)
def test_read_namespace(tmp_path: Path, content: str, expected: str | None) -> None:
    f = tmp_path / "namespace"
    f.write_text(content)
    assert read_namespace(f) == expected


def test_read_namespace_outside_kubernetes(tmp_path: Path) -> None:
    assert read_namespace(tmp_path / "missing") is None
