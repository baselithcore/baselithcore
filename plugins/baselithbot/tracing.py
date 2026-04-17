"""OpenTelemetry tracing wrapper integrated with ``core.observability``.

If the core observability package exposes a tracer factory, we use it;
otherwise we fall back to ``opentelemetry.trace`` direct or a no-op
context manager when the SDK is not installed.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator


def _resolve_tracer() -> Any:
    try:
        from opentelemetry import trace  # type: ignore[import-not-found]

        tracer = trace.get_tracer("baselithbot")
        if hasattr(tracer, "start_as_current_span"):
            return tracer
    except Exception:
        pass
    return None


_TRACER = _resolve_tracer()


@contextmanager
def trace_span(name: str, **attributes: Any) -> Iterator[Any]:
    """Yield an OpenTelemetry span if available, else a no-op context."""
    if _TRACER is None:
        yield None
        return
    with _TRACER.start_as_current_span(name) as span:
        for key, value in attributes.items():
            try:
                span.set_attribute(key, value)
            except Exception:
                pass
        yield span


def is_tracing_enabled() -> bool:
    return _TRACER is not None


__all__ = ["trace_span", "is_tracing_enabled"]
