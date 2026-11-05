"""Named catalog of workflow definitions.

The executor runs a :class:`WorkflowDefinition` you hand it; the catalog is
what lets a process *own* definitions by id - load them from a directory of
YAML/JSON files at startup, accept new ones through the API, and let the
trigger, scheduler and MCP surfaces find them by name.

Persistence is a plain directory, one ``<id>.yaml`` per workflow, so the
catalog can live in version control and be reviewed like code. With no
directory configured the catalog is memory-only.

Registration validates: a definition that does not pass
:meth:`WorkflowDefinition.validate` never enters the catalog, whatever the
route it arrived by.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from baselith.core.observability.logging import get_logger

from .builder import WorkflowDefinition
from .serialization import (
    DefinitionError,
    load_definition_file,
    write_definition_file,
)

logger = get_logger(__name__)

__all__ = [
    "CatalogError",
    "CatalogEvent",
    "LoadReport",
    "WorkflowCatalog",
    "is_valid_workflow_id",
]

#: A workflow id doubles as a file name and a URL segment.
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
_SUFFIXES = (".yaml", ".yml", ".json")

CatalogEvent = Callable[[str, str], None]


class CatalogError(ValueError):
    """A definition cannot enter (or be removed from) the catalog."""


def is_valid_workflow_id(workflow_id: str) -> bool:
    """Whether ``workflow_id`` is safe as a catalog key and file name."""
    return bool(_ID_PATTERN.match(workflow_id)) and ".." not in workflow_id


@dataclass
class LoadReport:
    """Outcome of :meth:`WorkflowCatalog.load_directory`."""

    loaded: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)


class WorkflowCatalog:
    """In-memory registry of workflows, optionally mirrored to a directory."""

    def __init__(
        self, directory: str | Path | None = None, *, max_workflows: int = 1000
    ) -> None:
        """
        Args:
            directory: Where definitions are loaded from and saved to; ``None``
                keeps the catalog memory-only.
            max_workflows: Refuse registrations past this many distinct ids.
        """
        self._directory = Path(directory).resolve() if directory else None
        self._max = max_workflows
        self._workflows: dict[str, WorkflowDefinition] = {}
        self._files: dict[str, list[Path]] = {}
        self._listeners: list[CatalogEvent] = []

    @property
    def directory(self) -> Path | None:
        """The backing directory, if any."""
        return self._directory

    def subscribe(self, listener: CatalogEvent) -> None:
        """Call ``listener(event, workflow_id)`` after each change.

        ``event`` is ``"registered"`` or ``"removed"``. A raising listener is
        logged and does not undo the change.
        """
        self._listeners.append(listener)

    def _notify(self, event: str, workflow_id: str) -> None:
        for listener in self._listeners:
            try:
                listener(event, workflow_id)
            except Exception as exc:
                logger.warning(
                    "workflow_catalog_listener_failed event=%s id=%s error=%s",
                    event,
                    workflow_id,
                    exc,
                )

    def get(self, workflow_id: str) -> WorkflowDefinition | None:
        """The definition registered under ``workflow_id``, if any."""
        return self._workflows.get(workflow_id)

    def list(self) -> list[WorkflowDefinition]:
        """Every definition, ordered by id."""
        return [self._workflows[key] for key in sorted(self._workflows)]

    def __len__(self) -> int:
        return len(self._workflows)

    def __contains__(self, workflow_id: object) -> bool:
        return workflow_id in self._workflows

    def register(self, workflow: WorkflowDefinition, *, persist: bool = False) -> bool:
        """Add or replace a definition.

        Args:
            workflow: The definition; its ``id`` is the catalog key.
            persist: Also write it to the directory as ``<id>.yaml``.

        Returns:
            Whether it was written to disk (``False`` for a memory-only
            catalog even when ``persist`` was asked for).

        Raises:
            CatalogError: Invalid id, failed validation, catalog full, or a
                write failure. Nothing changes in that case.
        """
        self._check(workflow)
        if workflow.id not in self._workflows and len(self._workflows) >= self._max:
            raise CatalogError(f"catalog is full ({self._max} workflows)")
        written = False
        if persist and self._directory is not None:
            written = self._write(workflow)
        self._workflows[workflow.id] = workflow
        self._notify("registered", workflow.id)
        return written

    @staticmethod
    def _check(workflow: WorkflowDefinition) -> None:
        if not is_valid_workflow_id(workflow.id):
            raise CatalogError(
                f"invalid workflow id {workflow.id!r}: use 1-100 letters, digits, "
                "'_', '-' or '.', starting with a letter or digit"
            )
        errors = workflow.validate()
        if errors:
            raise CatalogError("; ".join(errors))

    def _write(self, workflow: WorkflowDefinition) -> bool:
        assert self._directory is not None
        target = (self._directory / f"{workflow.id}.yaml").resolve()
        if target.parent != self._directory:  # defence in depth behind the id check
            raise CatalogError(f"invalid workflow id {workflow.id!r}")
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
            write_definition_file(workflow, target)
        except (OSError, DefinitionError) as exc:
            raise CatalogError(f"could not save {workflow.id!r}: {exc}") from exc
        for stale in self._files.get(workflow.id, []):
            if stale != target:
                stale.unlink(missing_ok=True)
        self._files[workflow.id] = [target]
        return True

    def remove(self, workflow_id: str) -> bool:
        """Drop a definition and delete its file(s).

        Returns:
            Whether the id was registered.
        """
        if self._workflows.pop(workflow_id, None) is None:
            return False
        for path in self._files.pop(workflow_id, []):
            path.unlink(missing_ok=True)
        self._notify("removed", workflow_id)
        return True

    def load_directory(self) -> LoadReport:
        """Register every definition file in the directory.

        One bad file is reported and skipped; it never stops the others. Two
        files declaring the same id are a conflict: the first (by file name)
        wins and the second is reported.
        """
        report = LoadReport()
        if self._directory is None or not self._directory.is_dir():
            return report
        for path in sorted(self._directory.iterdir()):
            if path.suffix.lower() not in _SUFFIXES or not path.is_file():
                continue
            try:
                workflow = load_definition_file(path)
                if workflow.id in self._files and self._files[workflow.id] != [path]:
                    raise CatalogError(
                        f"id {workflow.id!r} is already defined by "
                        f"{self._files[workflow.id][0].name}"
                    )
                self._check(workflow)
            except (CatalogError, DefinitionError, OSError) as exc:
                report.failed[path.name] = str(exc)
                logger.warning(
                    "workflow_definition_rejected file=%s error=%s", path.name, exc
                )
                continue
            if workflow.id not in self._workflows and len(self._workflows) >= self._max:
                report.failed[path.name] = f"catalog is full ({self._max} workflows)"
                continue
            self._workflows[workflow.id] = workflow
            self._files[workflow.id] = [path.resolve()]
            report.loaded.append(workflow.id)
            self._notify("registered", workflow.id)
        return report
