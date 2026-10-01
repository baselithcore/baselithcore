"""Default upgrade instructions per installation method.

Every command here is a standard tool of the installation method (``helm``,
``kubectl``, ``docker``, ``pip``, ``git``) or a documented framework command
(``baselith db migrate``, ``scripts/backup-db.sh``), with the target release
substituted. They are shown to the administrator, never run by the framework.
Angle-bracket words (``<release>``) are values only the administrator knows.

The templates describe the public distribution: the Helm chart is installed
from ``deploy/helm/baselithcore`` of the core repository at the release tag
(the project publishes no Helm repository), the image is
``ghcr.io/<owner>/<repo>:<version>``, the production compose file is
``compose.prod.yaml`` and the package is ``baselith-core``. A deployment whose
procedure differs documents it with method ``custom``; a downstream
distribution gets :func:`distribution_step` instead of these templates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..upgrade_models import InstallMethod, UpgradeStep


@dataclass(frozen=True)
class TemplateContext:
    """The values substituted into the templates."""

    version: str
    current: str
    repo: str
    namespace: str = "<namespace>"
    base_url: str = "<base-url>"
    compose_file: str = "compose.prod.yaml"

    @property
    def tag(self) -> str:
        """The release tag (``v`` + version)."""
        return f"v{self.version}"

    @property
    def job_suffix(self) -> str:
        """The version as a Kubernetes name part (``0.41.0`` -> ``0-41-0``)."""
        return re.sub(r"[^a-z0-9]+", "-", self.version.lower()).strip("-")

    @property
    def image(self) -> str:
        """The published container image of the target release."""
        return f"ghcr.io/{self.repo.lower()}:{self.version}"


def _helm(ctx: TemplateContext) -> list[UpgradeStep]:
    chart_dir = f"baselithcore-{ctx.version}"
    return [
        UpgradeStep(
            id="helm.find_release",
            text="Find the Helm release name and namespace of this deployment.",
            command="helm list --all-namespaces",
        ),
        UpgradeStep(
            id="helm.fetch_chart",
            text=f"Get the chart of release {ctx.version}: it is installed from "
            "the core repository at the release tag.",
            command=f"git clone --depth 1 --branch {ctx.tag} "
            f"https://github.com/{ctx.repo}.git {chart_dir}",
        ),
        UpgradeStep(
            id="helm.upgrade",
            text="Upgrade the release, keeping your values on top of the new "
            "chart's defaults. The chart's pre-upgrade Job applies the database "
            "migrations before new pods start; if your values pin image.digest, "
            "set the digest of the new image instead of the tag.",
            command=f"helm upgrade <release> ./{chart_dir}/deploy/helm/baselithcore "
            f"--namespace {ctx.namespace} --reset-then-reuse-values "
            f"--set image.tag={ctx.version} --wait --timeout 15m",
        ),
        UpgradeStep(
            id="helm.test",
            text="Run the chart's smoke test.",
            command=f"helm test <release> --namespace {ctx.namespace}",
        ),
    ]


def _compose(ctx: TemplateContext) -> str:
    return f"docker compose -f {ctx.compose_file}"


def _docker(ctx: TemplateContext) -> list[UpgradeStep]:
    compose = _compose(ctx)
    return [
        UpgradeStep(
            id="docker.build_from_tag",
            text=f"Option A, the image is built from a source checkout (the "
            f"shipped compose files build it): check out release {ctx.version}, "
            "rebuild and recreate the containers. The commands use "
            f"{ctx.compose_file}, the production stack; name your own compose "
            "file instead if it differs.",
            command=f"git fetch --tags && git checkout {ctx.tag} && "
            f"{compose} build && {compose} up -d",
        ),
        UpgradeStep(
            id="docker.prebuilt_image",
            text=f"Option B, run the published image instead: in your compose "
            f"file, set image: {ctx.image} on the api and worker services "
            "(in place of their build: section), then pull it and recreate the "
            "containers. Either way the API applies the database migrations at "
            "startup.",
            command=f"{compose} pull api worker && {compose} up -d",
        ),
        UpgradeStep(
            id="docker.migrate",
            text="Only with DB_MIGRATIONS_ON_STARTUP=false: apply the database "
            "migrations yourself.",
            command=f"{compose} exec api baselith db migrate",
        ),
    ]


def _pip(ctx: TemplateContext) -> list[UpgradeStep]:
    return [
        UpgradeStep(
            id="pip.install",
            text="Upgrade the package in the environment the service runs from "
            "(keep the extras you installed, e.g. baselith-core[rag]).",
            command=f'pip install --upgrade "baselith-core=={ctx.version}"',
        ),
        UpgradeStep(
            id="pip.migrate",
            text="Apply the database migrations.",
            command="baselith db migrate",
        ),
        UpgradeStep(
            id="pip.restart",
            text="Restart the API and worker processes (for example with "
            "systemctl restart and your service names).",
        ),
    ]


def _source(ctx: TemplateContext) -> list[UpgradeStep]:
    return [
        UpgradeStep(
            id="source.checkout",
            text=f"In the source checkout the service runs from, check out "
            f"release {ctx.version}.",
            command=f"git fetch --tags && git checkout {ctx.tag}",
        ),
        UpgradeStep(
            id="source.install",
            text="Reinstall it into the service's environment, so new "
            "dependencies are installed (keep the extras you installed, e.g. "
            ".[rag]).",
            command="pip install -e .",
        ),
        UpgradeStep(
            id="source.migrate",
            text="Apply the database migrations.",
            command="baselith db migrate",
        ),
        UpgradeStep(
            id="source.restart",
            text="Restart the API and worker processes (for example with "
            "systemctl restart and your service names).",
        ),
    ]


def distribution_step(distribution: str) -> UpgradeStep:
    """The one step of a downstream distribution: its own procedure applies."""
    return UpgradeStep(
        id="distribution.procedure",
        text=f"This deployment runs a downstream distribution ({distribution}): "
        "the public core procedure does not apply to it and would replace it "
        "with the public release. Set SYSTEM_UPGRADE_INSTRUCTIONS_FILE to the "
        "distribution's own upgrade procedure, which is then shown here, or "
        "SYSTEM_UPGRADE_GUIDE_URL to link it.",
    )


def default_steps(method: InstallMethod, ctx: TemplateContext) -> list[UpgradeStep]:
    """The upgrade steps of ``method``; ``custom`` has none of its own."""
    if method == "helm":
        return _helm(ctx)
    if method == "docker":
        return _docker(ctx)
    if method == "pip":
        return _pip(ctx)
    if method == "source":
        return _source(ctx)
    return []


def backup_step(method: InstallMethod, ctx: TemplateContext) -> UpgradeStep:
    """The pre-upgrade database backup for ``method``."""
    text = (
        "Back up the database before upgrading, and keep the backup until "
        "the new release is verified."
    )
    if method == "helm":
        return UpgradeStep(
            id="checklist.backup.helm",
            text="Back up the database: start a run of the chart's backup "
            "CronJob (backup.enabled; named <fullname>-backup) and wait for it.",
            command=f"kubectl create job --namespace {ctx.namespace} "
            f"--from=cronjob/<fullname>-backup baselithcore-before-{ctx.job_suffix}",
        )
    if method == "docker":
        return UpgradeStep(
            id="checklist.backup.docker",
            text="Back up the database with the backup script, from the "
            "repository checkout: it runs pg_dump through compose.prod.yaml and "
            "writes to /backups/postgres, so it needs root (or write access to "
            "/backups); with another compose file, run pg_dump in its postgres "
            "service instead.",
            command="./scripts/backup-db.sh",
        )
    if method in ("pip", "source"):
        return UpgradeStep(
            id=f"checklist.backup.{method}",
            text="Back up the PostgreSQL database with pg_dump.",
            command=f"pg_dump --format=custom "
            f"--file=baselith-before-{ctx.version}.dump <database-url>",
        )
    return UpgradeStep(id="checklist.backup", text=text)


def post_checks(ctx: TemplateContext) -> list[UpgradeStep]:
    """What to verify once the upgrade is done."""
    return [
        UpgradeStep(
            id="checklist.health",
            text="Check that the service reports ready.",
            command=f"curl -fsS {ctx.base_url}/health/ready",
        ),
        UpgradeStep(
            id="checklist.console_version",
            text=f"Check that the console's System updates section shows core "
            f"{ctx.version} as installed.",
        ),
    ]


__all__ = [
    "TemplateContext",
    "backup_step",
    "default_steps",
    "distribution_step",
    "post_checks",
]
