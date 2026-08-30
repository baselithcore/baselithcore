"""Facade wiring the skill-evolution loop to the event bus.

``start()`` subscribes to ``EVALUATION_COMPLETED`` so every evaluation
feeds the wiki (maintainer) and credits skill impact — this facade is the
ONLY distillation bridge, by design: a second subscriber would double-count
pattern occurrences. The propose→gate cycle is explicit
(:meth:`SkillEvolutionService.evolve`) so callers decide when skill
synthesis is worth an LLM call; synthesis without a validator is refused
(fail closed), and source patterns are promoted only after the gate
accepts, so rejected knowledge stays re-proposable.

Tenancy: patterns are stored per tenant (Postgres backend), but managed
skills land in one shared catalog served to every tenant. To keep
tenant-specific evaluation text out of cross-tenant skills, ``evolve()``
refuses to synthesize outside the default tenant unless explicitly allowed.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from core.context import get_tenant_or_default
from core.events import EventNames, get_event_bus
from core.observability.logging import get_logger
from core.skill_evolution.gating import SkillGate
from core.skill_evolution.impact import SkillImpactTracker
from core.skill_evolution.maintainer import WikiMaintainer, safe_run_id, safe_score
from core.skill_evolution.proposer import SkillProposer
from core.skill_evolution.store import InMemoryPatternStore, PatternStore
from core.skill_evolution.types import GateDecision, PatternStatus
from core.skill_evolution.writer import ManagedSkillWriter

logger = get_logger(__name__)

__all__ = [
    "SkillEvolutionService",
    "build_skill_evolution_service",
    "make_activation_guard",
]

#: The only tenant whose patterns may be compiled into (shared) skills by
#: default — see the module docstring.
_DEFAULT_TENANT = "default"


class SkillEvolutionService:
    """Compose maintainer, proposer, gate, and impact tracking."""

    def __init__(
        self,
        store: PatternStore,
        writer: ManagedSkillWriter,
        *,
        maintainer: WikiMaintainer | None = None,
        proposer: SkillProposer | None = None,
        gate: SkillGate | None = None,
        impact: SkillImpactTracker | None = None,
    ) -> None:
        self.store = store
        self.writer = writer
        self.maintainer = maintainer
        self.proposer = proposer
        self.gate = gate
        self.impact = impact or SkillImpactTracker()
        self._running = False
        self._unsubscribe: Callable[[], None] | None = None

    def start(self) -> None:
        """Subscribe to evaluation events (idempotent)."""
        if self._running:
            return
        self._running = True
        self._unsubscribe = get_event_bus().subscribe(
            EventNames.EVALUATION_COMPLETED, self._on_evaluation
        )
        logger.info("SkillEvolutionService started")

    def stop(self) -> None:
        """Unsubscribe from evaluation events."""
        self._running = False
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        logger.info("SkillEvolutionService stopped")

    async def _on_evaluation(self, data: dict[str, Any]) -> None:
        """Feed one evaluation into the wiki and the impact tracker.

        Unscored payloads (missing/None/non-numeric score) are skipped
        entirely — never fabricated into 0.0 failures.
        """
        if not self._running:
            return
        score = safe_score(data)
        if score is None:
            return
        run_id = safe_run_id(data)
        try:
            self.impact.record_outcome(score, run_id=run_id)
            if self.maintainer is not None:
                await self.maintainer.distill_evaluation(data)
        except Exception as exc:
            logger.warning(f"Skill-evolution evaluation handling failed: {exc}")

    async def evolve(
        self,
        validate: Callable[[str], Awaitable[float]] | None = None,
        *,
        allow_tenant_synthesis: bool = False,
        autonomy_policy: Any | None = None,
        human_intervention: Any | None = None,
    ) -> GateDecision | None:
        """Run one propose→gate cycle.

        Requires a configured proposer, gate, AND ``validate`` — synthesis
        without validation is refused (fail closed). Source patterns are
        promoted only when the gate accepts; a rejected proposal leaves
        them CANDIDATE, the skill rolled back, and its source patterns on
        the proposer's rejection cooldown.

        Args:
            validate: Async validator (``skill_name -> score`` or
                ``skill_name -> FitnessVector``).
            allow_tenant_synthesis: Allow synthesis while a non-default
                tenant context is bound. Off by default: managed skills are
                shared across tenants, so compiling one tenant's pattern
                text into them is a data-leak vector.
            autonomy_policy: When provided, an eval-accepted skill still
                requires human approval under the ``self_modify`` category
                before it stands — "anything that changes future behavior
                gets human review". Denied/unavailable approval rolls the
                version back (fail closed). ``None`` keeps today's
                eval-gate-only behavior.
            human_intervention: Approval channel consulted by the policy.

        Returns:
            The gate's decision, or None when refused or no proposal ripened.
        """
        tenant = get_tenant_or_default()
        if tenant != _DEFAULT_TENANT and not allow_tenant_synthesis:
            logger.warning(
                "Skill synthesis refused for tenant '%s': managed skills are "
                "shared; pass allow_tenant_synthesis=True to override.",
                tenant,
            )
            return None
        if self.proposer is None or self.gate is None or validate is None:
            logger.warning(
                "Skill synthesis refused: proposer, gate, and validator are "
                "all required (fail closed)."
            )
            return None

        proposal = await self.proposer.propose()
        if proposal is None:
            return None
        await self._audit_propose(proposal)
        decision = await self.gate.review(proposal.name, validate)
        if decision.accepted and autonomy_policy is not None:
            decision = await self._require_human_approval(
                proposal.name, decision, autonomy_policy, human_intervention
            )
        if decision.accepted:
            for pattern_id in proposal.source_pattern_ids:
                await self.store.set_status(pattern_id, PatternStatus.PROMOTED)
        else:
            self.proposer.record_rejection(proposal.source_pattern_ids)
        return decision

    async def _require_human_approval(
        self,
        skill_name: str,
        decision: GateDecision,
        policy: Any,
        human: Any | None,
    ) -> GateDecision:
        """Hold an eval-accepted skill for human review (fail closed)."""
        from core.orchestration.autonomy import SELF_MODIFY, enforce_approval

        try:
            await enforce_approval(policy, SELF_MODIFY, skill_name, human)
            return decision
        except Exception as exc:
            logger.warning(
                "Skill '%s' passed the eval gate but was not approved for "
                "self-modification (%s); rolling back.",
                skill_name,
                exc,
            )
            rolled_back = await self.writer.rollback(skill_name)
            try:
                from core.observability.audit import (
                    AuditEventType,
                    get_audit_logger,
                )

                await get_audit_logger().log(
                    AuditEventType.SELF_MODIFY_ROLLBACK,
                    resource=skill_name,
                    action="skill_evolution.approval_rollback",
                    details={"reason": str(exc), "rolled_back": rolled_back},
                    success=False,
                )
            except Exception:  # pragma: no cover - observability only
                logger.debug("skill_rollback_audit_failed", exc_info=True)
            return decision.model_copy(
                update={"accepted": False, "rolled_back": rolled_back}
            )

    async def _audit_propose(self, proposal: Any) -> None:
        """Record the synthesis proposal on the audit trail (never raises)."""
        try:
            from core.observability.audit import AuditEventType, get_audit_logger

            await get_audit_logger().log(
                AuditEventType.SELF_MODIFY_PROPOSE,
                resource=proposal.name,
                action="skill_evolution.propose",
                details={"source_patterns": list(proposal.source_pattern_ids)},
            )
        except Exception:  # pragma: no cover - observability only
            logger.debug("skill_propose_audit_failed", exc_info=True)

    def get_stats(self) -> dict[str, Any]:
        """Runtime stats: running flag + per-skill impact snapshot."""
        return {
            "running": self._running,
            "impact": {
                name: impact.model_dump()
                for name, impact in self.impact.stats().items()
            },
        }


def make_activation_guard(
    writer: ManagedSkillWriter,
) -> Callable[[Any, str], str | None]:
    """Build a catalog activation guard enforcing managed-skill integrity.

    The guard receives ``(card, body)`` from
    :meth:`core.plugins.skills_service.SkillService.activate`. For cards
    living under the writer's root it verifies the on-disk content against
    the SHA-256 recorded at write/rollback time (the same fail-closed model
    as plugin ``integrity_sha256``); every other card passes untouched.
    """

    def guard(card: Any, _body: str) -> str | None:
        path = getattr(card, "path", None)
        if not isinstance(path, Path):
            return None
        try:
            path.resolve().relative_to(writer.root.resolve())
        except (ValueError, OSError):
            return None  # not a managed skill
        if writer.verify_path_sync(path):
            return None
        return (
            f"Managed skill '{getattr(card, 'name', '?')}' failed integrity "
            "verification (content does not match the recorded hash)."
        )

    return guard


def build_skill_evolution_service(
    *,
    root: Path | None = None,
    generate: Callable[[str], Awaitable[str]] | None = None,
    rca: Callable[[str], Awaitable[str]] | None = None,
) -> SkillEvolutionService:
    """Build a service with default wiring.

    Uses the Postgres pattern store when Postgres is enabled in storage
    config, the in-memory store otherwise (config/import errors only — a
    DB that is enabled but unreachable surfaces at first use, it does NOT
    silently downgrade). The proposer exists only when ``generate`` is
    supplied (skill synthesis needs an LLM).

    Args:
        root: Managed-skills root. Defaults to
            ``<CoreConfig.data_dir>/skills/managed``.
        generate: Optional async LLM callable for skill synthesis.
        rca: Optional async root-cause analyzer for failure summaries.

    Returns:
        A ready (not yet started) :class:`SkillEvolutionService`.
    """
    store = _default_store()
    writer = ManagedSkillWriter(root or _default_root())
    proposer = (
        SkillProposer(store, writer, generate=generate)
        if generate is not None
        else None
    )
    return SkillEvolutionService(
        store,
        writer,
        maintainer=WikiMaintainer(store, rca=rca),
        proposer=proposer,
        gate=SkillGate(writer),
    )


def _default_root() -> Path:
    try:
        from core.config import get_core_config

        return Path(get_core_config().data_dir) / "skills" / "managed"
    except Exception:  # pragma: no cover - config optional in minimal setups
        return Path("data") / "skills" / "managed"


def _default_store() -> PatternStore:
    try:
        from core.config import get_storage_config

        if get_storage_config().postgres_enabled:
            from core.skill_evolution.store_postgres import PostgresPatternStore

            return PostgresPatternStore()
    except Exception as exc:  # pragma: no cover - config/backend optional
        logger.warning(f"Falling back to in-memory pattern store: {exc}")
    return InMemoryPatternStore()
