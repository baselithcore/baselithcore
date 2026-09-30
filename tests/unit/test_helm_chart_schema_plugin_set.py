"""The plugin-schema Job builds the schema of the release's plugin set only.

`plugins.config` is the declarative set a release runs. The api and worker
pods read it from a ConfigMap mounted over /app/configs/plugins.yaml, but that
ConfigMap is an ordinary resource: Helm applies it *after* the
pre-install/pre-upgrade hooks, so the schema Job cannot mount it. Without its
own copy the Job read the image's plugins.yaml, took every plugin in the image
as enabled, and ran `init_schema()` for plugins the release never loads — so a
first install could fail on a plugin it does not even run, for instance one
writing its data dir on the Job's read-only root filesystem.
"""

from __future__ import annotations

import yaml

from tests.unit.helm import render

ENABLED = ("--set", "database.pluginSchemaInit.enabled=true")
PLUGIN_SET = (
    "--set",
    "plugins.config.auth.enabled=true",
    "--set",
    "plugins.config.compliance.enabled=true",
)


def _docs(*args: str) -> list[dict]:
    return [d for d in yaml.safe_load_all(render(*ENABLED, *args)) if d]


def _schema_job(docs: list[dict]) -> dict:
    return next(
        d
        for d in docs
        if d["kind"] == "Job"
        and d["metadata"]["labels"]["app.kubernetes.io/component"] == "plugin-schema"
    )


def _hook_copy(docs: list[dict]) -> dict | None:
    return next(
        (
            d
            for d in docs
            if d["kind"] == "ConfigMap"
            and d["metadata"]["name"].endswith("-plugins-hook")
        ),
        None,
    )


class TestSchemaJobReadsThePluginSet:
    def test_a_hook_scoped_copy_exists_before_the_job(self) -> None:
        docs = _docs(*PLUGIN_SET)
        copy = _hook_copy(docs)
        assert copy is not None
        annotations = copy["metadata"]["annotations"]
        assert "pre-install" in annotations["helm.sh/hook"]
        assert "pre-upgrade" in annotations["helm.sh/hook"]
        job_weight = int(
            _schema_job(docs)["metadata"]["annotations"]["helm.sh/hook-weight"]
        )
        assert int(annotations["helm.sh/hook-weight"]) < job_weight
        assert yaml.safe_load(copy["data"]["plugins.yaml"]) == {
            "auth": {"enabled": True},
            "compliance": {"enabled": True},
        }

    def test_the_job_mounts_it_over_the_image_plugins_yaml(self) -> None:
        docs = _docs(*PLUGIN_SET)
        spec = _schema_job(docs)["spec"]["template"]["spec"]
        mount = next(
            m
            for m in spec["containers"][0]["volumeMounts"]
            if m["mountPath"] == "/app/configs/plugins.yaml"
        )
        assert mount["subPath"] == "plugins.yaml"
        assert mount.get("readOnly") is True
        volume = next(v for v in spec["volumes"] if v["name"] == mount["name"])
        assert volume["configMap"]["name"] == _hook_copy(docs)["metadata"]["name"]

    def test_without_a_plugin_set_nothing_is_added(self) -> None:
        docs = _docs()
        assert _hook_copy(docs) is None
        spec = _schema_job(docs)["spec"]["template"]["spec"]
        mounts = [m["mountPath"] for m in spec["containers"][0].get("volumeMounts", [])]
        assert "/app/configs/plugins.yaml" not in mounts

    def test_no_copy_when_the_job_is_off(self) -> None:
        docs = [
            d
            for d in yaml.safe_load_all(
                render("--set", "database.pluginSchemaInit.enabled=false", *PLUGIN_SET)
            )
            if d
        ]
        assert _hook_copy(docs) is None
