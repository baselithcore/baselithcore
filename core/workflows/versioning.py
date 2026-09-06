"""Pin a workflow definition to a run, so a deploy cannot rewrite it mid-flight.

A durable run outlives the process that started it. It also outlives the
*deployment* that started it: the checkpoint is resumed hours later, by a pod
running whatever definition the current release ships. If that definition
changed — a node inserted, an edge rerouted, a condition rewritten — the
resumed run replays recorded outputs into a graph that no longer matches them.
Nothing errors. The run simply takes a path it was never on, and the recorded
steps line up with the wrong nodes.

The fix is to write down *which* definition a run started with and check it on
resume:

    pinned = pin_version(workflow)              # at the first pass
    verify_pinned_version(workflow, pinned)     # on every resume

``version`` alone is not enough. It is a string a human maintains, and the
failure this guards against is precisely the edit nobody thought to bump a
version for. So a pin carries **both** the declared ``version`` and a
``fingerprint`` — a digest of the graph's executable structure. The fingerprint
ignores what cannot change a run's path (labels, canvas positions,
descriptions, timestamps) and covers what can: node ids, types, agent and tool
bindings, condition expressions, configs, retry policy, and every edge.

Enforcement is staged through ``BASELITH_WORKFLOW_VERSION_PINNING``, the same
shape as the plugin-permission rollout:

``off``
    Pins are still recorded; mismatches are ignored.
``warn``
    A mismatch is logged and the run continues — the observation window for an
    operator who suspects definitions are being edited under live runs.
``enforce`` *(default)*
    A mismatch stops the run. Safe as a default because a run that predates
    this module carries no pin: it is pinned on first sight and can never
    mismatch retroactively. Only a definition genuinely edited *during* a
    live run is refused.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from core.observability.logging import get_logger

if TYPE_CHECKING:
    from core.workflows.builder import WorkflowDefinition

logger = get_logger(__name__)

__all__ = [
    "PINNING_ENV_VAR",
    "WORKFLOW_VERSION_KEY",
    "VersionMismatchError",
    "VersionPinning",
    "WorkflowVersion",
    "definition_fingerprint",
    "pin_run_definition",
    "pin_version",
    "resolve_pinning_mode",
    "verify_pinned_version",
]

#: Key under which a pin is stored in ``Checkpoint.plugin_data``.
WORKFLOW_VERSION_KEY = "workflow_version"

#: Environment variable selecting the rollout stage.
PINNING_ENV_VAR = "BASELITH_WORKFLOW_VERSION_PINNING"


class VersionPinning(str, Enum):
    """How strictly a definition change under a live run is treated."""

    OFF = "off"
    WARN = "warn"
    ENFORCE = "enforce"

    @property
    def enforces(self) -> bool:
        """Whether a mismatch stops the run."""
        return self is VersionPinning.ENFORCE


class VersionMismatchError(RuntimeError):
    """A resumed run's definition is not the one it started with."""

    def __init__(
        self, workflow_id: str, pinned: WorkflowVersion, current: WorkflowVersion
    ) -> None:
        self.workflow_id = workflow_id
        self.pinned = pinned
        self.current = current
        super().__init__(
            f"workflow '{workflow_id}' changed while a run was in flight: "
            f"started on {pinned.describe()}, resumed against {current.describe()}"
        )


def resolve_pinning_mode(value: str | None = None) -> VersionPinning:
    """Map a configured value to a mode, defaulting to :attr:`VersionPinning.ENFORCE`.

    Args:
        value: Raw setting; ``None`` reads ``BASELITH_WORKFLOW_VERSION_PINNING``.

    Returns:
        The selected mode. An unrecognised value falls back to ``enforce``: a
        typo must not silently disable the guard, and the guard cannot affect
        a run that has no pin.
    """
    if value is None:
        value = os.environ.get(PINNING_ENV_VAR, "")
    normalized = (value or "").strip().lower()
    if normalized in {"off", "false", "0", "no", "none", "disabled"}:
        return VersionPinning.OFF
    if normalized in {"warn", "observe", "log"}:
        return VersionPinning.WARN
    return VersionPinning.ENFORCE


@dataclass(frozen=True)
class WorkflowVersion:
    """The identity of one definition: what it declares, and what it is."""

    version: str
    fingerprint: str

    def describe(self) -> str:
        """A short human-readable identity for logs and errors."""
        return f"version={self.version} fingerprint={self.fingerprint[:12]}"

    def to_dict(self) -> dict[str, str]:
        """A JSON-friendly view for checkpoint storage."""
        return {"version": self.version, "fingerprint": self.fingerprint}

    @classmethod
    def from_dict(cls, data: Any) -> WorkflowVersion | None:
        """Read a pin back, tolerating anything that is not one.

        A checkpoint written by an older release, or hand-edited, must not
        crash a resume: an unreadable pin is *no* pin, and the run is pinned
        afresh.
        """
        if not isinstance(data, dict):
            return None
        version = data.get("version")
        fingerprint = data.get("fingerprint")
        if not isinstance(version, str) or not isinstance(fingerprint, str):
            return None
        return cls(version=version, fingerprint=fingerprint)


def _executable_shape(workflow: WorkflowDefinition) -> dict[str, Any]:
    """The parts of a definition that can change what a run does.

    Deliberately excluded: ``label``, ``position``, ``name``, ``description``,
    ``metadata`` and the timestamps. Moving a node on a canvas or fixing a typo
    in a description must not invalidate every run in flight — a fingerprint
    that fires on cosmetic edits gets switched off, and then it guards nothing.
    """
    return {
        "nodes": sorted(
            (
                {
                    "id": node.id,
                    "type": node.type.value,
                    "config": node.config,
                    "agent_id": node.agent_id,
                    "tool_id": node.tool_id,
                    "condition_expression": node.condition_expression,
                    "timeout": node.timeout,
                    "retries": node.retries,
                    "retry_backoff": node.retry_backoff,
                }
                for node in workflow.nodes
            ),
            key=lambda entry: str(entry["id"]),
        ),
        "edges": sorted(
            (
                {
                    "id": edge.id,
                    "source_id": edge.source_id,
                    "target_id": edge.target_id,
                    "condition_label": edge.condition_label,
                }
                for edge in workflow.edges
            ),
            key=lambda entry: str(entry["id"]),
        ),
    }


def definition_fingerprint(workflow: WorkflowDefinition) -> str:
    """Digest the executable structure of ``workflow``.

    Args:
        workflow: The definition to fingerprint.

    Returns:
        A 64-character hex digest, stable across processes and independent of
        the order nodes and edges happen to be stored in. A value that cannot
        be serialised falls back to its ``repr`` rather than raising: a
        fingerprint that fails to compute would disable the guard for exactly
        the definitions most worth guarding.
    """
    shape = _executable_shape(workflow)
    canonical = json.dumps(shape, sort_keys=True, separators=(",", ":"), default=repr)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def pin_version(workflow: WorkflowDefinition) -> WorkflowVersion:
    """The pin to record for a run starting on ``workflow``."""
    return WorkflowVersion(
        version=workflow.version, fingerprint=definition_fingerprint(workflow)
    )


def verify_pinned_version(
    workflow: WorkflowDefinition,
    pinned: WorkflowVersion | None,
    *,
    mode: VersionPinning | None = None,
) -> WorkflowVersion:
    """Check ``workflow`` against the pin a run recorded when it started.

    Args:
        workflow: The definition this process is about to execute.
        pinned: The recorded pin, or ``None`` for a run that has none yet.
        mode: Rollout stage; ``None`` resolves the environment.

    Returns:
        The pin to store — the existing one when it matches, the current one
        when there was none (or the mode is not enforcing and the run is
        allowed to continue on the new definition).

    Raises:
        VersionMismatchError: The definition changed and the mode enforces.
    """
    current = pin_version(workflow)
    if pinned is None:
        return current
    if pinned == current:
        return pinned

    effective = mode if mode is not None else resolve_pinning_mode()
    if effective is VersionPinning.OFF:
        return pinned
    mismatch = VersionMismatchError(workflow.id, pinned, current)
    if effective.enforces:
        raise mismatch
    logger.warning(f"{mismatch} (continuing: pinning mode is 'warn')")
    return pinned


def pin_run_definition(workflow: WorkflowDefinition, checkpoint: Any) -> None:
    """Record, or verify, the definition a durable run is bound to.

    Only durable runs are pinned: without a checkpoint there is no resume, so
    there is nothing a later deploy could change underneath. The pin lives in
    ``Checkpoint.plugin_data``, which already survives a resume — no schema
    change, and a checkpoint written before this existed simply has no pin and
    gets one now.

    Args:
        workflow: The definition about to execute.
        checkpoint: A :class:`~core.orchestration.checkpoint.CheckpointManager`,
            or ``None`` for a non-durable run. Anything without reachable
            checkpoint state is a no-op rather than an error — the executor
            accepts an injected double here.

    Raises:
        VersionMismatchError: The definition changed mid-run and the pinning
            mode enforces.
    """
    state = getattr(checkpoint, "checkpoint", None)
    plugin_data = getattr(state, "plugin_data", None)
    if not isinstance(plugin_data, dict):
        return
    pinned = WorkflowVersion.from_dict(plugin_data.get(WORKFLOW_VERSION_KEY))
    plugin_data[WORKFLOW_VERSION_KEY] = verify_pinned_version(
        workflow, pinned
    ).to_dict()
