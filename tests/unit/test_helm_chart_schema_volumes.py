"""The plugin-schema Job reading a plugin set kept on an application volume.

`database.pluginSchemaInit.mountVolumes` gives the hook pod the release's
`extraVolumes`/`extraVolumeMounts` and its `seedFromImage` initContainer, so
a writable PLUGIN_CONFIG_PATH (the console's toggles rewrite it) is the file
the Job reads — the only way a deployment keeps those toggles *and* runs its
serving pods as a role that holds no DDL.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest
import yaml

from tests.unit.helm import CHART_DIR, render

# The Job's default differs between the enterprise and the public chart, so
# every render names it rather than inheriting whichever default this is.
ENABLED = ("--set", "database.pluginSchemaInit.enabled=true")

VOLUME_ARGS = (
    "--set",
    "extraVolumes[0].name=app-data",
    "--set",
    "extraVolumes[0].persistentVolumeClaim.claimName=app-data",
    "--set",
    "extraVolumeMounts[0].name=app-data",
    "--set",
    "extraVolumeMounts[0].mountPath=/app/data",
    "--set",
    "seedFromImage[0].from=/app/configs/plugins.yaml",
    "--set",
    "seedFromImage[0].to=/app/data/configs/plugins.yaml",
    "--set",
    "config.PLUGIN_CONFIG_PATH=/app/data/configs/plugins.yaml",
)


def _schema_job(*args: str) -> dict:
    docs = [d for d in yaml.safe_load_all(render(*ENABLED, *args)) if d]
    return next(
        d
        for d in docs
        if d["kind"] == "Job"
        and d["metadata"]["labels"]["app.kubernetes.io/component"] == "plugin-schema"
    )


def _render_result(*args: str) -> subprocess.CompletedProcess[str]:
    helm = shutil.which("helm")
    if helm is None:  # pragma: no cover — depends on the host toolchain
        pytest.skip("helm binary not available")
    return subprocess.run(
        [helm, "template", "release", str(CHART_DIR), *ENABLED, *args],
        capture_output=True,
        text=True,
        timeout=120,
    )


class TestMountVolumes:
    def test_off_by_default_so_a_volume_path_is_still_refused(self) -> None:
        result = _render_result(*VOLUME_ARGS)
        assert result.returncode != 0
        assert "mountVolumes" in result.stderr

    def test_the_job_mounts_the_volume_the_config_lives_on(self) -> None:
        spec = _schema_job(
            *VOLUME_ARGS, "--set", "database.pluginSchemaInit.mountVolumes=true"
        )["spec"]["template"]["spec"]
        mounts = {
            m["name"]: m["mountPath"] for m in spec["containers"][0]["volumeMounts"]
        }
        assert mounts["app-data"] == "/app/data"
        claims = {
            v["name"]: v.get("persistentVolumeClaim", {}).get("claimName")
            for v in spec["volumes"]
        }
        assert claims["app-data"] == "app-data"

    def test_the_seed_runs_first_so_a_first_install_reads_the_apps_copy(
        self,
    ) -> None:
        spec = _schema_job(
            *VOLUME_ARGS, "--set", "database.pluginSchemaInit.mountVolumes=true"
        )["spec"]["template"]["spec"]
        (seed,) = spec["initContainers"]
        assert seed["name"] == "seed-from-image"
        assert "/app/data/configs/plugins.yaml" in seed["command"][-1]

    def test_a_path_outside_every_mounted_volume_is_still_refused(self) -> None:
        result = _render_result(
            *VOLUME_ARGS,
            "--set",
            "database.pluginSchemaInit.mountVolumes=true",
            "--set",
            "config.PLUGIN_CONFIG_PATH=/app/elsewhere/plugins.yaml",
        )
        assert result.returncode != 0
        assert "mutually exclusive" in result.stderr

    def test_a_mount_path_prefix_is_not_a_parent_directory(self) -> None:
        """/app/database is not under /app/data."""
        result = _render_result(
            *VOLUME_ARGS,
            "--set",
            "database.pluginSchemaInit.mountVolumes=true",
            "--set",
            "config.PLUGIN_CONFIG_PATH=/app/database/plugins.yaml",
        )
        assert result.returncode != 0

    def test_without_it_the_job_mounts_no_application_volume(self) -> None:
        spec = _schema_job()["spec"]["template"]["spec"]
        assert "initContainers" not in spec
        names = {v["name"] for v in spec.get("volumes", [])}
        assert names <= {"tmp"}


class TestRuntimeRoleCreatedOutsideTheChart:
    """`createRole: false`: the owner running the Job has no CREATEROLE."""

    @staticmethod
    def _script(*args: str) -> str:
        docs = [
            d
            for d in yaml.safe_load_all(
                render(
                    "--set",
                    "database.runtimeRole.enabled=true",
                    "--set",
                    "database.runtimeRole.adminSecret.name=owner",
                    *args,
                )
            )
            if d
        ]
        job = next(
            d
            for d in docs
            if d["kind"] == "Job"
            and d["metadata"]["labels"]["app.kubernetes.io/component"]
            == "db-runtime-role"
        )
        return job["spec"]["template"]["spec"]["containers"][0]["command"][-1]

    def test_default_still_creates_and_repairs_the_role(self) -> None:
        script = self._script()
        assert "CREATE ROLE" in script and "ALTER ROLE" in script

    def test_verifies_instead_of_creating(self) -> None:
        script = self._script("--set", "database.runtimeRole.createRole=false")
        assert "CREATE ROLE" not in script and "ALTER ROLE" not in script
        for refusal in (
            "does not exist",
            "SUPERUSER or BYPASSRLS",
            "owns this database",
        ):
            assert refusal in script
        # Kubernetes turns `$$` in a container command into `$`; psql then
        # sees `DO $` and the Job fails with a syntax error.
        assert "$$" not in script
        # The grants are unchanged: they are what the owner may do.
        assert "GRANT SELECT, INSERT, UPDATE, DELETE" in script
        assert "ALTER DEFAULT PRIVILEGES" in script
