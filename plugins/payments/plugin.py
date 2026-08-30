"""Payments plugin: exposes a reference PSP adapter for the AP2 seam.

The protocol and orchestration (``execute_payment``, receipts, delegated
mode) live in ``core.world_model``; PSP adapters are integrations and
therefore live here, behind the
:class:`core.world_model.payments.PaymentExecutor` protocol.
"""

from __future__ import annotations

from typing import Any

from core.plugins import Plugin

from .mock_psp import MockPSPAdapter


class PaymentsPlugin(Plugin):
    """Plugin wiring a :class:`MockPSPAdapter` into the orchestration layer.

    Configuration keys (plugin load-time config):
        decline_over_cents: int — cart totals above this are declined by the
            bundled mock adapter. Omit to capture everything.
    """

    def __init__(self) -> None:
        """Initialize the plugin with no executor until ``initialize``."""
        super().__init__()
        self._executor: MockPSPAdapter | None = None

    async def initialize(self, config: dict[str, Any]) -> None:
        """Build the executor from the plugin configuration."""
        await super().initialize(config)
        raw = config.get("decline_over_cents")
        threshold = int(raw) if raw is not None else None
        self._executor = MockPSPAdapter(decline_over_cents=threshold)

    async def shutdown(self) -> None:
        """Drop the executor."""
        self._executor = None
        await super().shutdown()

    def get_executor(self) -> MockPSPAdapter:
        """Return the configured executor (a default one pre-lifecycle)."""
        if self._executor is None:
            self._executor = MockPSPAdapter()
        return self._executor


__all__ = ["PaymentsPlugin"]
