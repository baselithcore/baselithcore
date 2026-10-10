"""Importing the deprecated ``baselith.core.agents`` / ``baselith.core.goals`` shims must warn.

Both packages are ``deprecated`` in ``baselith.core.stability`` but used to import
silently, so nobody still on them was ever told.
"""

from __future__ import annotations

import importlib
import warnings

import pytest


@pytest.mark.parametrize(
    ("module", "alternative"),
    [
        ("baselith.core.agents", "baselith.plugins.browser_agent"),
        ("baselith.core.goals", "baselith.plugins.goals"),
    ],
)
def test_import_emits_deprecation_warning(module, alternative):
    package = importlib.import_module(module)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        importlib.reload(package)

    messages = [
        str(w.message) for w in caught if issubclass(w.category, DeprecationWarning)
    ]
    assert any(
        f"{module} is deprecated since 0.39 and will be removed in 1.0" in m
        and alternative in m
        for m in messages
    ), messages


def test_reexports_still_resolve():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from baselith.core.agents import BrowserAgent, CodingAgent
        from baselith.core.goals import Goal, GoalStatus, GoalTracker
        from baselith.plugins.goals import GoalTracker as PluginGoalTracker

    assert BrowserAgent and CodingAgent and Goal and GoalStatus
    assert GoalTracker is PluginGoalTracker
