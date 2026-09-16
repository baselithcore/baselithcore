"""The Job that builds every enabled plugin's schema at deploy time.

A deployment that isolates tenants at the database connects as a
least-privilege role, which owns nothing and holds no DDL. A plugin that
creates its tables from the serving process cannot live there, so the chart
runs `baselith plugin schema-init` as the owner instead — after the migrations,
before the application."""

from __future__ import annotations

import yaml

from tests.unit.helm import render


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
