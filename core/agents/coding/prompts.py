"""Backward-compatible shim for the Coding Agent prompts module."""

import sys

import plugins.coding_agent.prompts as _prompts

sys.modules[__name__] = _prompts
