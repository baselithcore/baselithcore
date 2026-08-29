"""
Skill catalog service with progressive disclosure.

Aggregates declarative ``SKILL.md`` skills shipped by plugins (convention:
``plugins/<name>/skills/**/SKILL.md``) into a single catalog the agentic
loop can surface to the model. Only the lightweight cards (name +
description) enter the system prompt; the heavy Markdown body is loaded
on demand when the model activates a specific skill — the same
progressive-disclosure contract as :mod:`core.plugins.declarative`.

Safety posture:

- Reads are sandboxed to the discovered plugin skill roots (the loader
  re-validates on every activation).
- A skill declaring ``requires_approval: true`` is gated through
  :class:`core.human.HumanIntervention` and fails closed when no approval
  channel is configured.
- Activated bodies pass through the indirect-injection scanner
  (detection-first) before reaching the model.
- Activations emit ``gen_ai.*`` spans for observability.

Results use the canonical :class:`core.plugins.result.SkillResult`
envelope so downstream code can branch deterministically.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core.observability.logging import get_logger

from .declarative import (
    DeclarativeSkillLoader,
    LoadedSkill,
    SkillCard,
    SkillLoadError,
    SkillSandboxError,
)
from .result import SkillResult, fail, ok, partial

if TYPE_CHECKING:
    from core.human import HumanIntervention

    from .registry import PluginRegistry

logger = get_logger(__name__)

#: Seconds a discovered catalog stays fresh before the roots are re-walked.
CATALOG_TTL_SECONDS: float = 30.0

ACTIVATE_SKILL_TOOL_NAME = "activate_skill"
ACTIVATE_SKILL_TOOL_DESCRIPTION = (
    "Load the full instructions of a listed skill by name before using it. "
    "Usage: activate_skill(<skill name>)."
)


class SkillService:
    """Catalog + activation facade over every plugin-shipped skill root."""

    def __init__(
        self,
        registry: PluginRegistry,
        *,
        catalog_ttl: float = CATALOG_TTL_SECONDS,
        extra_roots: Iterable[tuple[str, Path]] = (),
        on_activate: Callable[[str], None] | None = None,
        activation_guard: Callable[[SkillCard, str], str | None] | None = None,
    ) -> None:
        """Create the service.

        Args:
            registry: Plugin registry providing per-plugin skill roots.
            catalog_ttl: Seconds before the catalog is re-walked.
            extra_roots: Additional ``(label, directory)`` skill roots walked
                after the plugin roots — e.g. the managed root where evolved
                skills land (``core.skill_evolution``). A missing directory
                is skipped silently: it appears only after the first write.
                On a duplicate name the plugin-shipped skill wins.
            on_activate: Observer called with the skill name after every
                successful activation (impact tracking). Fail-soft.
            activation_guard: Policy hook called with ``(card, body)`` after
                the body is loaded; returning an error string BLOCKS the
                activation (fail closed). Used e.g. to enforce content-hash
                integrity on evolved managed skills
                (``core.skill_evolution.make_activation_guard``).
        """
        self._registry = registry
        self._catalog_ttl = catalog_ttl
        self._extra_roots = tuple(extra_roots)
        self._on_activate = on_activate
        self._activation_guard = activation_guard
        self._cards: dict[str, SkillCard] = {}
        self._loaders: dict[str, DeclarativeSkillLoader] = {}
        self._refreshed_at: float | None = None
        # Negative cache: names that survived a forced refresh and were still
        # unknown. Without it every lookup of an unknown name re-walked the
        # whole catalog (sync os.walk + read_text over all plugin roots).
        # Cleared on every refresh, so a newly added skill is visible after
        # at most one TTL window.
        self._missing_names: set[str] = set()

    # ------------------------------------------------------------------
    # Catalog
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """Re-walk every plugin skill root and rebuild the catalog.

        Fail-soft per plugin: one malformed SKILL.md disables that plugin's
        skills (with a warning), never the whole catalog. On a duplicate
        skill name the first provider (plugins sorted by name) wins.
        """
        cards: dict[str, SkillCard] = {}
        loaders: dict[str, DeclarativeSkillLoader] = {}
        roots = sorted(self._skill_roots().items())
        roots += [(label, root) for label, root in self._extra_roots if root.is_dir()]
        for plugin_name, root in roots:
            try:
                loader = DeclarativeSkillLoader([root])
                plugin_cards = loader.discover()
            except (SkillLoadError, SkillSandboxError) as exc:
                logger.warning("Skipping skills of plugin '%s': %s", plugin_name, exc)
                continue
            for card in plugin_cards:
                if card.name in cards:
                    logger.warning(
                        "Duplicate skill name '%s' from plugin '%s' ignored "
                        "(already provided by plugin '%s')",
                        card.name,
                        plugin_name,
                        cards[card.name].plugin,
                    )
                    continue
                cards[card.name] = replace(card, plugin=plugin_name)
                loaders[card.name] = loader
        self._cards = cards
        self._loaders = loaders
        self._missing_names.clear()
        self._refreshed_at = time.monotonic()

    def catalog(self) -> list[SkillCard]:
        """Return the current skill cards (cached for ``catalog_ttl``)."""
        self._ensure_fresh()
        return sorted(self._cards.values(), key=lambda c: c.name)

    def get_card(self, name: str) -> SkillCard | None:
        """Return the card for ``name``, or None when unknown."""
        self._ensure_fresh()
        card = self._cards.get(name)
        if card is None and name not in self._missing_names:
            # A skill added after the last walk should be visible without
            # waiting a full TTL window — retry once with a forced refresh.
            # A name STILL unknown afterwards goes on the negative cache so
            # repeated bad lookups don't re-walk the catalog until the next
            # refresh.
            self.refresh()
            card = self._cards.get(name)
            if card is None:
                self._missing_names.add(name)
        return card

    def render_catalog(self) -> str:
        """Render the catalog as a prompt-ready Markdown block.

        Empty string when no plugin ships skills, so callers can skip the
        section entirely.
        """
        cards = self.catalog()
        if not cards:
            return ""
        lines = [
            "## Available skills",
            "Skills are specialized instruction sets. Before performing a "
            f"task a skill covers, call {ACTIVATE_SKILL_TOOL_NAME}"
            '("<name>") to load its full instructions.',
        ]
        for card in cards:
            suffix = " (requires human approval)" if card.requires_approval else ""
            version = f" v{card.version}" if card.version else ""
            lines.append(f"- {card.name}{version}{suffix}: {card.description}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Activation
    # ------------------------------------------------------------------

    async def activate(
        self,
        name: str,
        *,
        human_intervention: HumanIntervention | None = None,
        available_tools: Iterable[str] | None = None,
    ) -> SkillResult:
        """Load a skill body, honoring its approval gate and tool contract.

        Args:
            name: Skill name as listed in the catalog.
            human_intervention: Approval channel for skills declaring
                ``requires_approval``. Missing channel ⇒ fail closed.
            available_tools: When provided, the skill's declared ``tools``
                are validated against it; missing tools degrade the result
                to ``partial`` so the model knows the skill cannot fully run.

        Returns:
            ``ok`` with ``{name, plugin, version, tools, body}`` on success,
            ``partial`` when declared tools are unavailable, ``fail``
            otherwise (unknown skill, approval denied/unavailable,
            unreadable body).
        """
        card = self.get_card(name)
        if card is None:
            known = ", ".join(sorted(self._cards)) or "none"
            return fail(
                f"Unknown skill '{name}'. Available skills: {known}.",
                error_code="skill_not_found",
            )

        if card.requires_approval:
            gate = await self._enforce_approval(card, human_intervention)
            if gate is not None:
                return gate

        span_attributes = {
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": ACTIVATE_SKILL_TOOL_NAME,
            "gen_ai.baselith.skill_name": card.name,
            "gen_ai.baselith.skill_plugin": card.plugin or "",
        }
        tracer = _get_tracer()
        with tracer.start_span("skill.activate", attributes=span_attributes) as span:
            try:
                loaded: LoadedSkill = self._loaders[card.name].activate(card.path)
            except (SkillLoadError, SkillSandboxError, KeyError) as exc:
                span.set_attribute("error.type", type(exc).__name__)
                logger.error("Skill '%s' activation failed: %s", card.name, exc)
                return fail(
                    f"Skill '{card.name}' could not be loaded.",
                    error_code="skill_load_error",
                )
            body = _scan_body(loaded.body, card.name)
            span.set_attribute("gen_ai.baselith.skill_body_chars", len(body))

        if self._activation_guard is not None:
            try:
                veto = self._activation_guard(card, body)
            except Exception as exc:
                logger.error("Activation guard errored for '%s': %s", card.name, exc)
                veto = f"Activation guard failure for skill '{card.name}'."
            if veto is not None:
                logger.error("Skill '%s' activation blocked: %s", card.name, veto)
                return fail(veto, error_code="skill_guard_rejected")

        data: dict[str, Any] = {
            "name": card.name,
            "plugin": card.plugin,
            "version": card.version,
            "tools": list(card.tools),
            "body": body,
        }
        self._notify_activation(card.name)
        missing = _missing_tools(card, available_tools)
        if missing:
            return partial(
                data,
                f"Skill '{card.name}' activated, but declared tools are "
                f"unavailable in this run: {', '.join(missing)}.",
                error_code="skill_tools_unavailable",
            )
        return ok(data, f"Skill '{card.name}' activated.")

    async def _enforce_approval(
        self,
        card: SkillCard,
        human_intervention: HumanIntervention | None,
    ) -> SkillResult | None:
        """Fail-closed approval gate. None means approved."""
        if human_intervention is None:
            return fail(
                f"Skill '{card.name}' requires human approval but no "
                "approval channel is configured.",
                error_code="skill_approval_required",
            )
        approved = await human_intervention.request_approval(
            f"Activate skill '{card.name}' (plugin '{card.plugin}'): "
            f"{card.description}",
            context={"skill": card.name, "plugin": card.plugin},
        )
        if not approved:
            return fail(
                f"Skill '{card.name}' activation denied by human reviewer.",
                error_code="skill_approval_denied",
            )
        return None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _skill_roots(self) -> dict[str, Any]:
        return self._registry.get_all_skill_roots()

    def _notify_activation(self, name: str) -> None:
        """Fire the activation observer; a broken observer never blocks."""
        if self._on_activate is None:
            return
        try:
            self._on_activate(name)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("on_activate hook failed for skill '%s': %s", name, exc)

    def _ensure_fresh(self) -> None:
        now = time.monotonic()
        if self._refreshed_at is None or now - self._refreshed_at > self._catalog_ttl:
            self.refresh()


def make_activation_tool_fn(
    service: SkillService,
    *,
    human_intervention: HumanIntervention | None = None,
    available_tools: Iterable[str] | None = None,
) -> Callable[..., Awaitable[str]]:
    """Build the ``activate_skill`` callable for tool registries.

    Accepts the skill name positionally (ReAct string-args dispatch) or as
    the ``name`` keyword (:class:`~core.orchestration.parallel.ParallelToolExecutor`).
    Returns a plain string — the loaded instructions or an actionable error —
    since tool observations are text.
    """

    async def activate_skill(name: str = "") -> str:
        if not name.strip():
            return (
                f"Error: {ACTIVATE_SKILL_TOOL_NAME} requires a skill name, "
                f'e.g. {ACTIVATE_SKILL_TOOL_NAME}("code-review").'
            )
        result = await service.activate(
            name.strip(),
            human_intervention=human_intervention,
            available_tools=available_tools,
        )
        if result.data and isinstance(result.data, dict) and result.data.get("body"):
            header = f"# Skill activated: {result.data['name']}\n"
            note = f"\nNOTE: {result.message}" if not result.success else ""
            return f"{header}{result.data['body']}{note}"
        return f"Error: {result.message}"

    return activate_skill


def _missing_tools(card: SkillCard, available_tools: Iterable[str] | None) -> list[str]:
    if available_tools is None or not card.tools:
        return []
    available = set(available_tools)
    return [t for t in card.tools if t not in available]


def _scan_body(body: str, skill_name: str) -> str:
    """Detection-first indirect-injection scan of a skill body."""
    try:
        from core.guardrails import scan_external_content

        return scan_external_content(body, source=f"skill:{skill_name}")
    except Exception:  # pragma: no cover - guardrails must never block skills
        return body


def _get_tracer() -> Any:
    from core.observability.tracing import get_tracer

    return get_tracer("core.plugins.skills")


__all__ = [
    "ACTIVATE_SKILL_TOOL_DESCRIPTION",
    "ACTIVATE_SKILL_TOOL_NAME",
    "CATALOG_TTL_SECONDS",
    "SkillService",
    "make_activation_tool_fn",
]
