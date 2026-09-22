"""The Job that builds every enabled plugin's schema at deploy time.

A deployment that isolates tenants at the database connects as a
least-privilege role, which owns nothing and holds no DDL. A plugin that
creates its tables from the serving process cannot live there, so the chart
runs `baselith plugin schema-init` as the owner instead — after the migrations,
before the application."""

from __future__ import annotations

import shutil
import subprocess

import pytest
import yaml

from tests.unit.helm import CHART_DIR, render


class TestPluginSchemaJob:
    """Plugin schema is deploy work, and its order is the whole design.

    Alembic first (weight 0), then the least-privilege role exists (1), then
    the plugins build their own tables as the owner (2) — and only then does
    Helm apply the Deployment. Get the order wrong and either the role is
    missing when the grants run, or the application starts against a schema
    that is not there yet.
    """

    @staticmethod
    def _jobs(*args: str) -> dict[str, dict]:
        rendered = render(
            "--set",
            "database.pluginSchemaInit.enabled=true",
            "--set",
            "database.runtimeRole.enabled=true",
            "--set",
            "database.runtimeRole.adminSecret.name=pg-owner",
            "--set",
            "database.runtimeRole.adminUser=owner",
            *args,
        )
        return {
            doc["metadata"]["labels"]["app.kubernetes.io/component"]: doc
            for doc in yaml.safe_load_all(rendered)
            if doc and doc["kind"] == "Job"
        }

    def test_absent_by_default(self) -> None:
        components = {
            doc["metadata"]["labels"].get("app.kubernetes.io/component")
            for doc in yaml.safe_load_all(render())
            if doc and doc["kind"] == "Job"
        }
        assert "plugin-schema" not in components

    def test_runs_after_the_migrations_and_the_role(self) -> None:
        jobs = self._jobs()
        weight = {
            name: int(doc["metadata"]["annotations"]["helm.sh/hook-weight"])
            for name, doc in jobs.items()
        }
        assert weight["migrations"] < weight["db-runtime-role"]
        assert weight["db-runtime-role"] < weight["plugin-schema"]

    def test_it_runs_the_command_as_the_owner(self) -> None:
        container = self._jobs()["plugin-schema"]["spec"]["template"]["spec"][
            "containers"
        ][0]
        assert container["command"] == ["baselith", "plugin", "schema-init"]
        env = {item["name"]: item for item in container["env"]}
        # Same override the migration Job takes: the serving role could not
        # create a table even if it tried.
        assert env["DB_USER"]["value"] == "owner"
        assert env["DB_PASSWORD"]["valueFrom"]["secretKeyRef"]["name"] == "pg-owner"

    @staticmethod
    def _render_result(plugin_config_path: str) -> subprocess.CompletedProcess[str]:
        """Render with the Job on and the plugin set at ``plugin_config_path``."""
        helm = shutil.which("helm")
        if helm is None:  # pragma: no cover — depends on the host toolchain
            pytest.skip("helm binary not available")
        return subprocess.run(
            [
                helm,
                "template",
                "release",
                str(CHART_DIR),
                "--set",
                "database.pluginSchemaInit.enabled=true",
                "--set",
                f"config.PLUGIN_CONFIG_PATH={plugin_config_path}",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )

    def test_refuses_a_plugin_set_the_job_cannot_read(self) -> None:
        """A hook pod mounts no application volume.

        Point PLUGIN_CONFIG_PATH at one and the file is simply absent when the
        Job runs: every plugin reads as enabled with an empty config block, and
        a plugin that picks its storage in that block builds the wrong one —
        `<plugin>: schema ready`, no table, exit 0, and an application that
        then boots against a schema that is not there.
        """
        result = self._render_result("/app/data/configs/plugins.yaml")
        assert result.returncode != 0
        assert "mutually exclusive" in result.stderr
        assert "plugins.config" in result.stderr

    def test_allows_a_plugin_set_that_rides_inside_the_image(self) -> None:
        """The Job runs the same image, so an in-image path it does carry."""
        result = self._render_result("/app/configs/plugins.yaml")
        assert result.returncode == 0, result.stderr[-2000:]
