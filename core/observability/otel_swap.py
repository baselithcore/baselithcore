"""A span processor whose downstream pipeline can be replaced at runtime.

OpenTelemetry lets the global ``TracerProvider`` be set exactly once per
process: a second ``trace.set_tracer_provider`` call is refused with a warning
and the old provider stays global. So a telemetry bootstrap that is shut down
and set up again (an app whose lifespan runs twice in one process, a test
suite) cannot install a new provider. It can only re-use the first one.

The bootstrap therefore installs one :func:`build_swappable_processor` on the
provider when it creates it, and every later setup swaps the exporters behind
it. Shutting the processor down flushes and closes the current generation of
exporters but leaves the processor itself usable for the next generation.

The SDK is imported inside the factory, so this module imports fine in a
checkout without OpenTelemetry.
"""

from __future__ import annotations

import threading
from typing import Any

__all__ = ["build_swappable_processor"]


def build_swappable_processor() -> Any:
    """Build a span processor that forwards to a replaceable set of processors.

    The returned object exposes ``replace(processors)``, which installs a new
    generation of downstream processors and shuts the previous one down.

    Returns:
        An ``opentelemetry.sdk.trace.SpanProcessor`` instance.

    Raises:
        ImportError: When the OpenTelemetry SDK is not installed.
    """
    from opentelemetry.sdk.trace import SpanProcessor, SynchronousMultiSpanProcessor

    class SwappableSpanProcessor(SpanProcessor):  # type: ignore[misc]
        """Delegates every hook to the current generation of processors."""

        def __init__(self) -> None:
            self._lock = threading.Lock()
            self._inner = SynchronousMultiSpanProcessor()

        def replace(self, processors: list[Any]) -> None:
            """Install ``processors`` and shut the previous generation down."""
            fresh = SynchronousMultiSpanProcessor()
            for processor in processors:
                fresh.add_span_processor(processor)
            with self._lock:
                old, self._inner = self._inner, fresh
            old.shutdown()

        def on_start(self, span: Any, parent_context: Any = None) -> None:
            self._inner.on_start(span, parent_context=parent_context)

        def _on_ending(self, span: Any) -> None:
            self._inner._on_ending(span)

        def on_end(self, span: Any) -> None:
            self._inner.on_end(span)

        def shutdown(self) -> None:
            # Close the current exporters but stay attached to the provider,
            # so the next setup can hand this processor a new generation.
            self.replace([])

        def force_flush(self, timeout_millis: int = 30_000) -> bool:
            flushed: bool = self._inner.force_flush(timeout_millis)
            return flushed

    return SwappableSpanProcessor()
