"""``database.pluginSchemaInit.extraEnv``: env for the plugin-schema Job only.

A plugin whose schema step needs a credential of the table owner — an engine
with a database of its own, whose owner URL is ``<PREFIX>SCHEMA_DATABASE_URL``
— must get it in that hook Job and nowhere else: the api and worker pods run as
the least-privilege role precisely so they cannot step outside a tenant's
policy. The entries come after the owner's ``DB_USER``/``DB_PASSWORD``, so a
``$(DB_PASSWORD)`` in a value expands to the owner's password.
"""

from __future__ import annotations

from typing import Any

import yaml

from tests.unit.helm import render

# The Job's default differs between the enterprise and the public chart, so
# every render names it rather than inheriting whichever default this is.
ENABLED = ("--set", "database.pluginSchemaInit.enabled=true")

ENTRY = (
    "--set",
    "database.pluginSchemaInit.extraEnv[0].name=ENGINE_SCHEMA_URL",
    "--set-string",
    "database.pluginSchemaInit.extraEnv[0].value=postgresql://owner:$(DB_PASSWORD)@db/x",
)
RUNTIME_ROLE = (
    "--set",
    "database.runtimeRole.enabled=true",
    "--set",
    "database.runtimeRole.name=app_rt",
    "--set",
    "database.runtimeRole.adminUser=owner",
    "--set",
    "database.runtimeRole.adminSecret.name=owner-secret",
)


def _docs(*args: str) -> list[dict[str, Any]]:
    return [d for d in yaml.safe_load_all(render(*ENABLED, *args)) if d]


def _component(docs: list[dict[str, Any]], component: str) -> dict[str, Any]:
    return next(
        d
        for d in docs
        if d["kind"] == "Job"
        and d["metadata"]["labels"]["app.kubernetes.io/component"] == component
    )


def _env_names(doc: dict[str, Any]) -> list[str]:
    container = doc["spec"]["template"]["spec"]["containers"][0]
    return [e["name"] for e in container.get("env") or []]


def test_the_entries_follow_the_owner_credential() -> None:
    docs = _docs(*ENTRY, *RUNTIME_ROLE)
    names = _env_names(_component(docs, "plugin-schema"))
    assert names == ["DB_USER", "DB_PASSWORD", "ENGINE_SCHEMA_URL"]


def test_they_render_without_a_runtime_role_too() -> None:
    names = _env_names(_component(_docs(*ENTRY), "plugin-schema"))
    assert names == ["ENGINE_SCHEMA_URL"]


def test_no_other_workload_gets_them() -> None:
    docs = _docs(*ENTRY, *RUNTIME_ROLE)
    others = [
        d
        for d in docs
        if d["kind"] in ("Deployment", "Job", "CronJob")
        and d["metadata"]["labels"].get("app.kubernetes.io/component")
        != "plugin-schema"
    ]
    assert others
    for doc in others:
        assert "ENGINE_SCHEMA_URL" not in yaml.safe_dump(doc), doc["metadata"]["name"]


def test_the_default_renders_no_env_block_of_its_own() -> None:
    job = _component(_docs(), "plugin-schema")
    assert "env" not in job["spec"]["template"]["spec"]["containers"][0]
