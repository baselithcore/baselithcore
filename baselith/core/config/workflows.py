"""
Workflow catalog and runtime configuration.

Controls the HTTP surface and runtime around :mod:`baselith.core.workflows`: the
catalog directory, the signed inbound trigger, the cron scheduler, run limits
and the outbound-HTTP posture of workflow HTTP nodes. Opt-in and default-off,
so a deployment that does not use catalog workflows exposes nothing new.
"""

from pydantic import Field, SecretStr
from pydantic_settings import SettingsConfigDict

from baselith.core.config._aliased import AliasedSettings
from baselith.core.config.overrides import overridable


class WorkflowsConfig(AliasedSettings):
    """Configuration for the workflow catalog, triggers and scheduler."""

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    # Master switch: loads the catalog, mounts the /workflows router and
    # registers opted-in workflows as MCP tools.
    enabled: bool = Field(default=False, alias="WORKFLOWS_ENABLED")

    # Directory of ``*.yaml`` / ``*.yml`` / ``*.json`` definitions loaded at
    # startup. Definitions saved through the API are written back here; without
    # it the catalog is memory-only and an API upload is lost on restart.
    directory: str | None = Field(default=None, alias="WORKFLOWS_DIR")

    # Shared secret of the inbound trigger (POST /workflows/{id}/trigger).
    # Unset means the trigger answers 503: an unsigned trigger is never served.
    trigger_secret: SecretStr | None = Field(
        default=None, alias="WORKFLOWS_TRIGGER_SECRET"
    )
    # Oldest X-Baselith-Signature timestamp a trigger accepts, in seconds
    # (replay window). A repeated signature inside the window is refused too.
    trigger_tolerance_seconds: int = Field(
        default=300, alias="WORKFLOWS_TRIGGER_TOLERANCE_SECONDS", ge=1
    )

    # Run catalog workflows that carry a cron ``schedule`` inside this process.
    # Leave it off on all but one replica, or each replica fires the schedule.
    scheduler_enabled: bool = Field(default=False, alias="WORKFLOWS_SCHEDULER_ENABLED")
    scheduler_interval_seconds: float = Field(
        default=30.0, alias="WORKFLOWS_SCHEDULER_INTERVAL_SECONDS", ge=1
    )

    # Wall-clock limit of one run started through the API, a trigger or MCP.
    run_timeout_seconds: float = Field(
        default=300.0, alias="WORKFLOWS_RUN_TIMEOUT_SECONDS", gt=0
    )
    # Runs in flight at once in this process; further starts answer 429.
    max_concurrent_runs: int = Field(
        default=8, alias="WORKFLOWS_MAX_CONCURRENT_RUNS", ge=1
    )
    # Finished runs kept for GET /workflows/runs/{id} (process-local, oldest
    # evicted first).
    run_history_size: int = Field(default=500, alias="WORKFLOWS_RUN_HISTORY_SIZE", ge=1)

    # Credentials HTTP nodes may name (``config['credential']``), as one JSON
    # object: {"crm": {"Authorization": "Bearer ..."}}. A definition only ever
    # carries the name; the secret stays here, wrapped so it never reaches a
    # repr, a log line or an exported definition.
    http_credentials: SecretStr | None = Field(
        default=None, alias="WORKFLOWS_HTTP_CREDENTIALS"
    )

    # Allow CODE nodes, which run a Python snippet in the sandbox (Docker or
    # microVM). Off by default: a definition can be uploaded through the API,
    # so a node that runs code turns "may save a workflow" into "may run code".
    code_enabled: bool = Field(default=False, alias="WORKFLOWS_CODE_ENABLED")

    # SSRF: HTTP nodes reject loopback/private/link-local/reserved targets by
    # default. Enable only for trusted local development.
    http_allow_internal: bool = Field(
        default=False, alias="WORKFLOWS_HTTP_ALLOW_INTERNAL"
    )


_workflows_config: WorkflowsConfig | None = None


@overridable(WorkflowsConfig)
def get_workflows_config() -> WorkflowsConfig:
    """Get or create the global workflows configuration instance."""
    global _workflows_config
    if _workflows_config is None:
        _workflows_config = WorkflowsConfig()
    return _workflows_config
