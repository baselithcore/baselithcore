"""``BUILTIN_INTENTS`` must match what an orchestrator actually registers.

The constant exists so a reader can list the framework's own agents without
constructing an orchestrator (expensive and stateful). That trade is only safe
while the two agree, and nothing in the type system enforces it — a new
built-in handler added to ``Orchestrator.__init__`` would otherwise leave the
constant silently short, and every catalogue reading it silently wrong.
"""

from __future__ import annotations

from core.orchestration import BUILTIN_INTENTS
from core.orchestration.orchestrator import Orchestrator
from core.plugins.registry import PluginRegistry


def test_constant_matches_a_real_orchestrator() -> None:
    """An orchestrator over an empty registry registers exactly these."""
    orchestrator = Orchestrator(plugin_registry=PluginRegistry())

    assert sorted(orchestrator.get_registered_intents()) == sorted(BUILTIN_INTENTS)
