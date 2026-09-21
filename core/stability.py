"""API stability tiers: what each part of ``core`` promises its callers.

The versioning policy defines a breaking change as removing or renaming a
public symbol, and the public API surface gate enforces it over every literal
``__all__`` in the tree. Applied uniformly that is a straitjacket: it gives the
Ed25519 mandate chain and a research Monte-Carlo sketch the same contract, so
the only way to retire an experiment is a MAJOR release. A framework that
cannot retire an experiment stops publishing them.

Tiers fix that by saying out loud what was previously implied. Every top-level
package under ``core/`` is classified in :data:`PACKAGE_STABILITY`, and the
tier decides what it costs to change it:

===============  ==================================================  ===========
Tier             Promise                                             Removal
===============  ==================================================  ===========
``stable``       Shape is settled. Build on it.                      MAJOR only
``beta``         Production-ready, shape may still move.             MINOR, after a deprecation cycle
``experimental``  Research surface. No compatibility promise.        any MINOR
``deprecated``   Compatibility shim, already superseded.             MAJOR, announced
===============  ==================================================  ===========

The tier is a property of the *package*, not of every name it contains. A
symbol re-exported by the :mod:`baselith` facade is ``stable`` whatever its
package's tier: the facade is the contract, ``core.*`` is where the
implementation happens to live today. ``LoopBudget`` is stable as
``baselith.LoopBudget`` while ``core.orchestration`` as a whole stays ``beta``.

An individual symbol can also carry its own tier with :func:`stable`,
:func:`beta` or :func:`experimental`, for the experiment that lives inside a
settled package or the one piece of a research package that other code already
depends on. :func:`deprecated` marks a symbol on its way out and emits the
``DeprecationWarning`` the policy requires, so the announcement is a decorator
rather than a hand-written ``warnings.warn`` that each author spells
differently.

Nothing here imports anything: the table is data, read by
``scripts/check_public_api.py`` (which never imports the tree) and by the
documentation build.
"""

from __future__ import annotations

import functools
import inspect
import warnings
from collections.abc import Callable, Mapping
from enum import StrEnum
from typing import Any, Final, TypeVar

__all__ = [
    "PACKAGE_STABILITY",
    "STABILITY_ATTRIBUTE",
    "Stability",
    "beta",
    "deprecated",
    "experimental",
    "stability_of",
    "stable",
    "symbol_stability",
]

#: Attribute a decorated symbol carries its tier in.
STABILITY_ATTRIBUTE: Final = "__baselith_stability__"


class Stability(StrEnum):
    """How much a public surface promises its callers."""

    STABLE = "stable"
    BETA = "beta"
    EXPERIMENTAL = "experimental"
    DEPRECATED = "deprecated"


#: Tier of every top-level package under ``core/``, and of the public facade.
#:
#: Kept central rather than as an ``__stability__`` literal in each
#: ``__init__.py`` for one reason: a table can be checked for *completeness*.
#: ``scripts/check_public_api.py`` asserts these keys are exactly the packages
#: that exist, so a new package cannot arrive unclassified and an entry cannot
#: outlive the package it describes. Sixty scattered literals give no such
#: guarantee.
#:
#: Tiers move toward stability and never away from it — the same ratchet the
#: other gates use. Promoting a package is a deliberate line in a diff;
#: demoting one is a breaking change to a promise already made.
PACKAGE_STABILITY: Final[Mapping[str, Stability]] = {
    # -- the public facade ------------------------------------------------
    "baselith": Stability.STABLE,
    # -- stable: the contract downstream code and plugins build on --------
    "core.agent": Stability.STABLE,
    "core.api": Stability.STABLE,
    "core.config": Stability.STABLE,
    "core.plugins": Stability.STABLE,
    # -- beta: production infrastructure, shape still settling ------------
    "core.a2a": Stability.BETA,
    "core.auth": Stability.BETA,
    "core.bootstrap": Stability.BETA,
    "core.cache": Stability.BETA,
    "core.chat": Stability.BETA,
    "core.cli": Stability.BETA,
    "core.compliance": Stability.BETA,
    "core.db": Stability.BETA,
    "core.di": Stability.BETA,
    "core.evaluation": Stability.BETA,
    "core.events": Stability.BETA,
    "core.feature_flags": Stability.BETA,
    "core.graph": Stability.BETA,
    "core.guardrails": Stability.BETA,
    "core.incidents": Stability.BETA,
    "core.interfaces": Stability.BETA,
    "core.lifecycle": Stability.BETA,
    "core.mcp": Stability.BETA,
    "core.memory": Stability.BETA,
    "core.middleware": Stability.BETA,
    "core.models": Stability.BETA,
    "core.observability": Stability.BETA,
    "core.orchestration": Stability.BETA,
    "core.personas": Stability.BETA,
    "core.privacy": Stability.BETA,
    "core.prompts": Stability.BETA,
    "core.quotas": Stability.BETA,
    "core.reasoning": Stability.BETA,
    "core.registries": Stability.BETA,
    "core.resilience": Stability.BETA,
    "core.security": Stability.BETA,
    "core.services": Stability.BETA,
    "core.storage": Stability.BETA,
    "core.task_queue": Stability.BETA,
    "core.tenancy": Stability.BETA,
    "core.utils": Stability.BETA,
    "core.webhooks": Stability.BETA,
    "core.workflows": Stability.BETA,
    "core.world_model": Stability.BETA,
    # -- experimental: research surface, no compatibility promise ---------
    "core.adversarial": Stability.EXPERIMENTAL,
    "core.exploration": Stability.EXPERIMENTAL,
    "core.finetuning": Stability.EXPERIMENTAL,
    "core.human": Stability.EXPERIMENTAL,
    "core.learning": Stability.EXPERIMENTAL,
    "core.loops": Stability.EXPERIMENTAL,
    "core.marketplace": Stability.EXPERIMENTAL,
    "core.meta": Stability.EXPERIMENTAL,
    "core.nlp": Stability.EXPERIMENTAL,
    "core.optimization": Stability.EXPERIMENTAL,
    "core.planning": Stability.EXPERIMENTAL,
    "core.prioritization": Stability.EXPERIMENTAL,
    "core.realtime": Stability.EXPERIMENTAL,
    "core.reflection": Stability.EXPERIMENTAL,
    "core.skill_evolution": Stability.EXPERIMENTAL,
    "core.swarm": Stability.EXPERIMENTAL,
    "core.thirdparty": Stability.EXPERIMENTAL,
    "core.transparency": Stability.EXPERIMENTAL,
    # -- deprecated: the frozen domain shims ------------------------------
    # The same set ``scripts/check_architecture_boundaries.py`` freezes under
    # FROZEN_CORE_PREFIXES: domain logic that predates the Sacred Core rule and
    # now belongs in a plugin. No new file is accepted in them; they exist so
    # existing imports keep resolving.
    "core.agents": Stability.DEPRECATED,
    "core.doc_sources": Stability.DEPRECATED,
    "core.goals": Stability.DEPRECATED,
    "core.routers": Stability.DEPRECATED,
    "core.scraper": Stability.DEPRECATED,
}

#: Tier assumed for a dotted name whose package is not in the table. Only
#: reachable for a module outside the scanned roots, since the gate asserts
#: the table is complete.
DEFAULT_STABILITY: Final = Stability.EXPERIMENTAL


def stability_of(dotted_name: str) -> Stability:
    """The tier that governs ``dotted_name``.

    Resolution walks from the most specific prefix to the least, so a
    subpackage can be classified apart from its parent while every other
    module inherits the parent's tier.

    Args:
        dotted_name: A module, package or qualified symbol name, e.g.
            ``"core.memory.hybrid_search"``.

    Returns:
        The declared tier, or :data:`DEFAULT_STABILITY` when nothing matches.

    Examples:
        >>> stability_of("core.memory.hybrid_search")
        <Stability.BETA: 'beta'>
        >>> stability_of("baselith")
        <Stability.STABLE: 'stable'>
    """
    parts = dotted_name.split(".")
    for stop in range(len(parts), 0, -1):
        tier = PACKAGE_STABILITY.get(".".join(parts[:stop]))
        if tier is not None:
            return tier
    return DEFAULT_STABILITY


T = TypeVar("T")


def _mark(obj: T, tier: Stability) -> T:
    """Attach ``tier`` to ``obj`` without disturbing anything else about it."""
    try:
        setattr(obj, STABILITY_ATTRIBUTE, tier)
    except (AttributeError, TypeError):
        # Builtins, slotted instances and C-level objects reject attributes.
        # The marker is documentation, never control flow: losing it must not
        # break the decorated symbol.
        pass
    return obj


def stable(obj: T) -> T:
    """Mark a symbol ``stable`` regardless of its package's tier."""
    return _mark(obj, Stability.STABLE)


def beta(obj: T) -> T:
    """Mark a symbol ``beta`` regardless of its package's tier."""
    return _mark(obj, Stability.BETA)


def experimental(obj: T) -> T:
    """Mark a symbol ``experimental`` regardless of its package's tier."""
    return _mark(obj, Stability.EXPERIMENTAL)


def symbol_stability(obj: Any, module: str | None = None) -> Stability:
    """The tier of a decorated symbol, falling back to its module's tier."""
    marked = getattr(obj, STABILITY_ATTRIBUTE, None)
    if isinstance(marked, Stability):
        return marked
    return stability_of(module or getattr(obj, "__module__", "") or "")


def _deprecation_message(
    name: str, since: str, removed_in: str, alternative: str | None
) -> str:
    """The one wording every deprecation in the tree uses."""
    message = f"{name} is deprecated since {since} and will be removed in {removed_in}"
    if alternative:
        message += f"; use {alternative} instead"
    return message + "."


def deprecated(
    *, since: str, removed_in: str, alternative: str | None = None
) -> Callable[[T], T]:
    """Announce a symbol's removal, per the deprecation process.

    Emits a ``DeprecationWarning`` on use — at call time for a function, at
    construction for a class — with the version it was announced in, the
    version it disappears in, and what to use instead. The policy requires
    exactly this warning; the decorator is what makes every instance of it
    read the same and stay greppable.

    Args:
        since: Version that announced the deprecation, e.g. ``"0.37"``.
        removed_in: Version that removes it, e.g. ``"1.0"``. Must be at least
            one MINOR later than ``since``; the overlap window is the point.
        alternative: Dotted name of the replacement, when there is one.

    Returns:
        A decorator that marks the symbol ``deprecated`` and warns on use.

    Examples:
        >>> @deprecated(since="0.37", removed_in="1.0", alternative="new_api")
        ... def old_api() -> None: ...
    """

    def decorate(obj: T) -> T:
        message = _deprecation_message(
            getattr(obj, "__qualname__", str(obj)), since, removed_in, alternative
        )
        if inspect.isclass(obj):
            original_init = obj.__init__

            @functools.wraps(original_init)
            def __init__(self: Any, *args: Any, **kwargs: Any) -> None:
                warnings.warn(message, DeprecationWarning, stacklevel=2)
                original_init(self, *args, **kwargs)

            obj.__init__ = __init__  # type: ignore[method-assign]
            _append_to_doc(obj, message)
            return _mark(obj, Stability.DEPRECATED)

        if callable(obj):

            @functools.wraps(obj)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                warnings.warn(message, DeprecationWarning, stacklevel=2)
                return obj(*args, **kwargs)  # type: ignore[operator]

            _append_to_doc(wrapper, message)
            return _mark(wrapper, Stability.DEPRECATED)  # type: ignore[return-value]

        return _mark(obj, Stability.DEPRECATED)

    return decorate


def _append_to_doc(obj: Any, message: str) -> None:
    """Put the deprecation in the docstring, where ``help()`` will show it."""
    existing = inspect.getdoc(obj) or ""
    admonition = f".. deprecated::\n    {message}"
    try:
        obj.__doc__ = f"{existing}\n\n{admonition}" if existing else admonition
    except (AttributeError, TypeError):
        pass
