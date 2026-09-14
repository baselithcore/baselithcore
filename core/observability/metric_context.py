"""Request-context enrichment for the Prometheus metrics.

Two things a raw ``prometheus_client`` metric cannot do on its own, added here
as thin proxies so **no call site changes**:

* **A bounded ``tenant`` label.** ``gen_ai_client_cost_usd_total`` without a
  tenant dimension answers "what did we spend" but never "who spent it", which
  is the question chargeback and abuse triage actually ask. Adding the raw
  tenant id would multiply every series by the tenant count, so the label is
  bounded by an allowlist (:class:`~core.config.observability.ObservabilityConfig`):
  listed tenants keep their id, everything else collapses to ``other``, and a
  call made outside any tenant context reports ``unknown``.
  :class:`TenantLabeledCounter` back-fills the value from the ambient tenant
  context, so the existing two-argument ``.labels(system, model)`` call sites
  keep working and gain the dimension for free.

* **Trace exemplars.** :class:`ExemplarHistogram` attaches the current
  ``trace_id`` to each observation. In Grafana that turns "p99 spiked at 14:02"
  into one click through to a trace inside that bucket. Exemplars are only
  rendered by the OpenMetrics exposition; the plain-text one drops them
  silently, which is harmless.

  Whether to attach one is decided **before** the observation, not retried
  after it: ``prometheus_client`` increments the bucket and the sum and only
  then validates the exemplar, so an observe-then-fall-back-and-observe-again
  shape counts one measurement twice. Two conditions are therefore checked up
  front — the exemplar fits the 128-rune OpenMetrics label budget
  (:func:`exemplar_is_acceptable`), and the metric's ``observe`` actually takes
  an ``exemplar`` keyword (:func:`_supports_exemplar`, for older clients).
  Either one failing yields a plain observation. Anything else the client
  rejects propagates — the alternative was double-counting.

Both proxies delegate every other attribute to the wrapped metric, so they stay
usable anywhere a ``Counter``/``Histogram`` was.
"""

from __future__ import annotations

import inspect
from typing import Any

from core.config.observability import get_observability_config

#: Label value for a tenant that is not on the allowlist.
TENANT_OTHER = "other"
#: Label value when no tenant context is bound (background work, boot-time).
TENANT_UNKNOWN = "unknown"
#: Exemplar label carrying the W3C trace id.
TRACE_ID_LABEL = "trace_id"

#: OpenMetrics caps an exemplar's label set at 128 runes (names + values).
#: prometheus_client enforces it *after* it has already incremented the bucket
#: and the sum, so a rejected exemplar used to leave the observation recorded
#: and then get recorded again by the fallback — one ``observe`` counted twice.
#: Validating here, before the call, is the only way to have both a fallback
#: and an honest count.
EXEMPLAR_MAX_RUNES = 128

#: Per-class memo for "does this metric child accept an ``exemplar`` kwarg?".
_EXEMPLAR_SUPPORT: dict[type, bool] = {}


def exemplar_is_acceptable(exemplar: dict[str, str]) -> bool:
    """Whether *exemplar* is within the OpenMetrics label-size limit."""
    try:
        return (
            0 < sum(len(k) + len(v) for k, v in exemplar.items()) <= EXEMPLAR_MAX_RUNES
        )
    except Exception:  # silent-ok: a malformed exemplar is simply not usable
        return False


def _supports_exemplar(child: Any) -> bool:
    """Whether *child*'s ``observe`` takes an ``exemplar`` keyword."""
    kind = type(child)
    cached = _EXEMPLAR_SUPPORT.get(kind)
    if cached is not None:
        return cached
    try:
        parameters = inspect.signature(kind.observe).parameters
        supported = "exemplar" in parameters or any(
            p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()
        )
    except (TypeError, ValueError):  # builtins and mocks without a signature
        supported = False
    _EXEMPLAR_SUPPORT[kind] = supported
    return supported


def _exemplars_enabled() -> bool:
    try:
        return bool(get_observability_config().metrics_exemplars_enabled)
    except Exception:  # silent-ok: no config ⇒ no exemplars
        return False


def _ambient_tenant() -> str | None:
    """Current tenant id, or ``None`` when there is no usable context.

    ``get_current_tenant_id`` raises under ``strict_tenant_isolation`` when
    nothing is bound; a metric label is not the place to surface that.
    """
    try:
        from core.context import get_current_tenant_id

        return get_current_tenant_id()
    except Exception:  # silent-ok: unbound tenant is a label, not an error
        return None


def resolve_tenant_label(tenant_id: str | None = None) -> str:
    """Return the bounded ``tenant`` label value for *tenant_id*.

    Args:
        tenant_id: Explicit tenant id. When omitted (or ``None``) the ambient
            tenant context is consulted.

    Returns:
        The tenant id when it is on ``METRICS_TENANT_LABEL_ALLOWLIST``,
        :data:`TENANT_OTHER` when it is a real but unlisted tenant, and
        :data:`TENANT_UNKNOWN` when no tenant could be determined.
    """
    resolved = tenant_id if tenant_id is not None else _ambient_tenant()
    if not resolved:
        return TENANT_UNKNOWN
    try:
        allowlist = get_observability_config().metrics_tenant_label_allowlist
    except Exception:  # silent-ok: no config ⇒ bounded bucket
        return TENANT_OTHER
    return resolved if resolved in allowlist else TENANT_OTHER


def current_exemplar() -> dict[str, str] | None:
    """Return ``{"trace_id": ...}`` for the active sampled span, else ``None``.

    Unsampled spans are skipped deliberately: an exemplar pointing at a trace
    the collector dropped is a dead link in every dashboard that follows it.
    """
    if not _exemplars_enabled():
        return None
    try:
        from opentelemetry import trace as otel_trace

        span = otel_trace.get_current_span()
        if span is None:
            return None
        context = span.get_span_context()
        if context is None or not context.is_valid:
            return None
        if not context.trace_flags.sampled:
            return None
        return {TRACE_ID_LABEL: format(int(context.trace_id), "032x")}
    except Exception:  # silent-ok: no usable trace context ⇒ plain observation
        return None


class _MetricProxy:
    """Base proxy that forwards everything it does not override."""

    __slots__ = ("_metric",)

    def __init__(self, metric: Any) -> None:
        self._metric = metric

    def __getattr__(self, name: str) -> Any:
        # Guard the slot itself: an unset ``_metric`` (during unpickling, or a
        # partially-constructed proxy) would otherwise recurse forever here.
        if name == "_metric":
            raise AttributeError(name)
        return getattr(self._metric, name)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"{type(self).__name__}({self._metric!r})"


class TenantLabeledCounter(_MetricProxy):
    """Counter proxy that back-fills and bounds a trailing ``tenant`` label.

    The wrapped counter must declare ``tenant`` as its **last** label name.
    ``.labels()`` accepts the call with or without it, in positional or keyword
    form, and always normalises the value through :func:`resolve_tenant_label`.
    """

    __slots__ = ("_names",)

    def __init__(self, metric: Any) -> None:
        super().__init__(metric)
        # Read once at construction rather than on every emit: this is
        # prometheus_client's private attribute, and touching it in the hot
        # path would make each increment depend on an internal that could
        # change under us mid-process.
        self._names: tuple[str, ...] = tuple(getattr(metric, "_labelnames", ()))
        if not self._names or self._names[-1] != "tenant":
            raise ValueError(
                f"{getattr(metric, '_name', metric)!r} must declare 'tenant' as "
                "its last label to be wrapped in TenantLabeledCounter"
            )

    def labels(self, *args: Any, **kwargs: Any) -> Any:
        """Return the child series, filling in a bounded ``tenant`` label."""
        names = self._names
        if kwargs and not args:
            values = dict(kwargs)
            values["tenant"] = resolve_tenant_label(values.get("tenant"))
            return self._metric.labels(**values)
        positional = list(args)
        if len(positional) == len(names) - 1:
            positional.append(None)
        if positional:
            positional[-1] = resolve_tenant_label(positional[-1])
        return self._metric.labels(*positional)


class _ExemplarChild:
    """Histogram child that attaches the ambient trace exemplar on observe."""

    __slots__ = ("_child",)

    def __init__(self, child: Any) -> None:
        self._child = child

    def observe(self, amount: float, exemplar: dict[str, str] | None = None) -> None:
        """Record *amount*, annotated with the current trace when available.

        The enrichment is decided *before* the call, never retried after it:
        prometheus_client increments the bucket and the sum and only then
        validates the exemplar, so an observe-then-fall-back-and-observe-again
        shape double-counts every rejected exemplar. Here an exemplar that is
        too large, or a client whose ``observe`` takes no such keyword, simply
        yields a plain observation — exactly one, either way.
        """
        resolved = exemplar if exemplar is not None else current_exemplar()
        if (
            resolved is not None
            and exemplar_is_acceptable(resolved)
            and _supports_exemplar(self._child)
        ):
            self._child.observe(amount, exemplar=resolved)
            return
        self._child.observe(amount)

    def __getattr__(self, name: str) -> Any:
        if name == "_child":  # see _MetricProxy.__getattr__
            raise AttributeError(name)
        return getattr(self._child, name)


class ExemplarHistogram(_MetricProxy):
    """Histogram proxy whose children attach a ``trace_id`` exemplar."""

    __slots__ = ()

    def labels(self, *args: Any, **kwargs: Any) -> _ExemplarChild:
        """Return an exemplar-aware child for the given label values."""
        return _ExemplarChild(self._metric.labels(*args, **kwargs))

    def observe(self, amount: float, exemplar: dict[str, str] | None = None) -> None:
        """Observe on an unlabelled histogram, with the ambient exemplar."""
        _ExemplarChild(self._metric).observe(amount, exemplar)


__all__ = [
    "TENANT_OTHER",
    "TENANT_UNKNOWN",
    "TRACE_ID_LABEL",
    "ExemplarHistogram",
    "TenantLabeledCounter",
    "current_exemplar",
    "resolve_tenant_label",
]
