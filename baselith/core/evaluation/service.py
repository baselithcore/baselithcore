"""
Online evaluation of production traffic.

:class:`EvaluationService` listens to ``FLOW_COMPLETED`` and evaluates a
deterministic sample of successful flows, off the request path:

1. **Sampling** — a stable hash of the run id against ``EVAL_ONLINE_SAMPLE_RATE``
   (per-intent overrides in ``EVAL_ONLINE_INTENT_SAMPLE_RATES``); evaluation
   flows are never evaluated.
2. **Backpressure** — sampled flows go to a bounded queue drained by
   ``EVAL_ONLINE_WORKERS`` workers. A full queue drops the sample and counts it
   (``mas_eval_online_dropped_total``); the event handler never waits.
3. **Checks** — cheap deterministic checks (:mod:`.online_checks`) score every
   sampled flow; the LLM judge (``CompositeEvaluator``) additionally scores the
   ``EVAL_ONLINE_JUDGE_RATE`` share and emits ``EVALUATION_COMPLETED`` as before.
4. **Metrics and monitor** — scores feed the ``mas_eval_online_*`` metrics and
   a per-intent rolling :class:`~.online_monitor.QualityMonitor`; a degradation
   (or recovery) emits ``EVALUATION_DEGRADED`` (``EVALUATION_RECOVERED``), logs,
   and flips the ``mas_eval_online_degraded`` gauge.
5. **Store** — opt-in persistence of the verdicts (:mod:`.online_store`), with
   a retention sweep.

Settings are read when :meth:`EvaluationService.start` runs, never at import.
"""

import asyncio
import time
from collections.abc import Callable
from typing import Any

from baselith.core.config.evaluation import EvaluationConfig, get_evaluation_config
from baselith.core.events import EventBus, EventNames, get_event_bus
from baselith.core.observability.logging import get_logger

from .online_checks import OnlineSample, run_online_checks
from .online_metrics import (
    IntentLabeler,
    record_drop,
    record_failure,
    record_flags,
    record_sample,
    record_score,
    set_degraded,
)
from .online_monitor import DEGRADED, QualityMonitor
from .online_sampling import OnlineSampler
from .online_store import (
    OnlineEvalRecord,
    OnlineEvalStore,
    build_online_store,
    stored_text,
)
from .protocols import EvaluationResult, Evaluator

logger = get_logger(__name__)

# Upper bound on concurrent judge evaluations so a burst of completed flows
# can't fan out unbounded LLM-judge calls and saturate the backend.
DEFAULT_MAX_CONCURRENT_EVALUATIONS = 8
#: Seconds between two retention sweeps of the online store.
RETENTION_SWEEP_INTERVAL_SECONDS = 3600.0
_DAY_SECONDS = 86_400.0


def _number(value: Any) -> float | None:
    return (
        float(value)
        if isinstance(value, (int, float)) and not isinstance(value, bool)
        else None
    )


class EvaluationService:
    """Sampled, bounded online evaluation of completed flows.

    Args:
        event_bus: Bus to subscribe to (defaults to the process bus).
        evaluator: LLM judge for judged samples; a ``CompositeEvaluator`` is
            built on first use when omitted.
        max_concurrent: Judge calls in flight at once.
        config: Settings; read from the environment at :meth:`start` when
            omitted.
        store: Result store; built from ``EVAL_ONLINE_STORE`` when omitted.
        monitor: Quality monitor; built from the settings when omitted.
    """

    def __init__(
        self,
        event_bus: EventBus | None = None,
        evaluator: Evaluator | None = None,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT_EVALUATIONS,
        *,
        config: EvaluationConfig | None = None,
        store: OnlineEvalStore | None = None,
        monitor: QualityMonitor | None = None,
    ) -> None:
        self.event_bus = event_bus or get_event_bus()
        self._evaluator = evaluator
        self._config_override = config
        self.config: EvaluationConfig | None = None
        self.store = store
        self.monitor = monitor
        self._running = False
        self._max_concurrent = max_concurrent
        # Created lazily so the semaphore and queue bind to the running loop.
        self._semaphore: asyncio.Semaphore | None = None
        self._queue: asyncio.Queue[OnlineSample] | None = None
        self._workers: list[asyncio.Task[None]] = []
        self._sweeper: asyncio.Task[None] | None = None
        self._unsubscribe: Callable[[], None] | None = None
        self._sampler = OnlineSampler(0.0)
        self._labeler = IntentLabeler(1)

    @property
    def evaluator(self) -> Evaluator:
        """The LLM judge, built on first use (no judge, no LLM client)."""
        if self._evaluator is None:
            from .judges import CompositeEvaluator

            self._evaluator = CompositeEvaluator()
        return self._evaluator

    @property
    def running(self) -> bool:
        """Whether the service is subscribed to ``FLOW_COMPLETED``."""
        return self._running

    def _get_semaphore(self) -> asyncio.Semaphore:
        """Lazily build the judge concurrency limiter bound to the active loop."""
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self._max_concurrent)
        return self._semaphore

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Read the settings and start listening (a no-op unless ``EVAL_ENABLED``)."""
        if self._running:
            return
        config = self._config_override or get_evaluation_config()
        if not config.enabled:
            logger.info("EvaluationService disabled by config")
            return
        self.config = config
        self._sampler = OnlineSampler(
            config.online_sample_rate,
            config.online_judge_rate,
            config.online_intent_sample_rates,
        )
        self._labeler = IntentLabeler(config.online_max_intents)
        if self.monitor is None:
            self.monitor = QualityMonitor(
                window=config.online_window,
                reference_window=config.online_reference_window,
                min_samples=config.online_min_samples,
                threshold=config.online_degradation_threshold,
                floor=config.online_quality_floor,
                recovery_margin=config.online_recovery_margin,
            )
        if self.store is None:
            self.store = build_online_store(
                config.online_store, max_records=config.online_store_max_records
            )
        self._unsubscribe = self.event_bus.subscribe(
            EventNames.FLOW_COMPLETED, self._on_flow_completed
        )
        self._running = True
        self._ensure_workers()
        logger.info(
            "EvaluationService started",
            extra={
                "sample_rate": config.online_sample_rate,
                "judge_rate": config.online_judge_rate,
                "store": config.online_store,
            },
        )

    def _ensure_workers(self) -> None:
        """Start the queue workers and the sweeper once a loop is running."""
        if self._workers or not self._running or self.config is None:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return  # started outside a loop: deferred to the first event
        self._queue = asyncio.Queue(maxsize=self.config.online_queue_size)
        self._workers = [
            asyncio.create_task(self._worker(), name=f"online-eval-{i}")
            for i in range(self.config.online_workers)
        ]
        if self.store is not None and self.config.online_retention_days > 0:
            self._sweeper = asyncio.create_task(
                self._sweep_loop(), name="online-eval-retention"
            )

    def stop(self) -> None:
        """Unsubscribe and cancel the workers; queued samples are dropped."""
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        self._running = False
        for task in self._background_tasks():
            task.cancel()
        self._workers = []
        self._sweeper = None
        self._queue = None
        logger.info("EvaluationService stopped")

    async def shutdown(self) -> None:
        """Stop and wait for the cancelled workers to finish unwinding."""
        tasks = self._background_tasks()
        self.stop()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _background_tasks(self) -> list[asyncio.Task[None]]:
        tasks = list(self._workers)
        if self._sweeper is not None:
            tasks.append(self._sweeper)
        return tasks

    # -- intake (request path: never blocks) --------------------------------

    async def _on_flow_completed(self, data: dict[str, Any]) -> None:
        """Sample a completed flow and queue it; drop it when saturated."""
        if not self._running or not data.get("success", False):
            return
        intent = data.get("intent")
        if not isinstance(intent, str) or not intent or intent.startswith("evaluation"):
            return
        query, response = data.get("query"), data.get("response")
        if not isinstance(query, str) or not isinstance(response, str):
            return
        run_id = data.get("run_id")
        run_id = run_id if isinstance(run_id, str) and run_id else None
        decision = self._sampler.decide(intent, run_id)
        if not decision.sampled:
            return
        self._ensure_workers()
        if self._queue is None:
            return
        tenant = data.get("tenant_id")
        context, guardrails = data.get("context"), data.get("guardrails")
        tokens = _number(data.get("tokens"))
        sample = OnlineSample(
            intent=intent,
            query=query,
            response=response,
            run_id=run_id,
            tenant_id=tenant if isinstance(tenant, str) and tenant else None,
            judged=decision.judged,
            context=context if isinstance(context, dict) else {},
            guardrails=guardrails if isinstance(guardrails, dict) else {},
            duration_ms=_number(data.get("duration_ms")),
            cost_usd=_number(data.get("cost_usd")),
            tokens=int(tokens) if tokens is not None else None,
        )
        try:
            self._queue.put_nowait(sample)
        except asyncio.QueueFull:
            record_drop("queue_full")
            return
        record_sample(self._labeler.label(intent))

    # -- evaluation (workers) ------------------------------------------------

    async def _worker(self) -> None:
        assert self._queue is not None
        queue = self._queue
        while True:
            sample = await queue.get()
            try:
                await self.process_sample(sample)
            except Exception as exc:
                record_failure("pipeline")
                logger.warning("online_eval_failed: %s", exc)
            finally:
                queue.task_done()

    async def process_sample(self, sample: OnlineSample) -> None:
        """Evaluate one sampled flow: checks, optional judge, monitor, store.

        Args:
            sample: The sampled flow.
        """
        config = self.config or get_evaluation_config()
        label = self._labeler.label(sample.intent)
        try:
            outcome = run_online_checks(
                sample, min_chars=config.online_min_answer_chars
            )
        except Exception as exc:
            record_failure("checks")
            logger.warning("online_eval_checks_failed: %s", exc)
            return
        record_score(label, "checks", outcome.score, outcome.quality.value)
        record_flags(label, outcome.flags)
        await self._observe(label, "checks", outcome.score)
        raw = config.online_store_raw_text
        await self._persist(
            OnlineEvalRecord(
                intent=sample.intent,
                source="checks",
                score=outcome.score,
                quality=outcome.quality.value,
                run_id=sample.run_id,
                tenant_id=sample.tenant_id or "default",
                aspects=outcome.aspects,
                flags=outcome.flags,
                signals=outcome.signals,
                query_text=stored_text(sample.query) if raw else None,
                response_text=stored_text(sample.response) if raw else None,
            )
        )
        if not sample.judged or not sample.response.strip():
            return
        result = await self._evaluate_interaction(
            sample.query,
            sample.response,
            sample.context,
            sample.intent,
            run_id=sample.run_id,
            tenant_id=sample.tenant_id,
        )
        if result is None:
            return
        record_score(label, "judge", result.score, result.quality.value)
        await self._observe(label, "judge", result.score)
        await self._persist(
            OnlineEvalRecord(
                intent=sample.intent,
                source="judge",
                score=result.score,
                quality=result.quality.value,
                run_id=sample.run_id,
                tenant_id=sample.tenant_id or "default",
                aspects=dict(result.aspects),
            )
        )

    async def _observe(self, label: str, source: str, score: float) -> None:
        """Feed the monitor; announce a degradation or recovery."""
        if self.monitor is None:
            return
        try:
            transition = self.monitor.observe(label, source, score)
            if transition is None:
                return
            degraded = transition.state == DEGRADED
            set_degraded(label, source, degraded)
            payload = transition.to_event()
            if degraded:
                logger.warning("online_eval_degraded", extra=payload)
                await self.event_bus.emit(EventNames.EVALUATION_DEGRADED, payload)
            else:
                logger.info("online_eval_recovered", extra=payload)
                await self.event_bus.emit(EventNames.EVALUATION_RECOVERED, payload)
        except Exception as exc:
            record_failure("monitor")
            logger.warning("online_eval_monitor_failed: %s", exc)

    async def _persist(self, record: OnlineEvalRecord) -> None:
        if self.store is None:
            return
        try:
            await self.store.save(record)
        except Exception as exc:
            record_failure("store")
            logger.warning("online_eval_store_failed: %s", exc)

    async def _evaluate_interaction(
        self,
        query: str,
        response: str,
        context: dict[str, Any],
        intent: str,
        semaphore: asyncio.Semaphore | None = None,
        run_id: str | None = None,
        tenant_id: str | None = None,
    ) -> EvaluationResult | None:
        """Run the LLM judge and emit its verdict; ``None`` when it failed."""
        # Fall back to the service-wide limiter when called directly (e.g. tests
        # or ad-hoc evaluation), so the method stays usable without a caller-
        # supplied semaphore while still bounding the event-driven fan-out.
        semaphore = semaphore or self._get_semaphore()
        try:
            async with semaphore:
                await self.event_bus.emit(
                    EventNames.EVALUATION_STARTED, {"intent": intent}
                )
                result: EvaluationResult = await self.evaluator.evaluate(
                    response=response, query=query, context=context
                )

            await self.event_bus.emit(
                EventNames.EVALUATION_COMPLETED,
                {
                    "intent": intent,
                    "score": result.score,
                    "quality": result.quality.value,
                    "feedback": result.feedback,
                    "aspects": result.aspects,
                    "should_refine": result.should_refine,
                    "metadata": result.metadata,
                    # Consumed by learning/skill-evolution distillation:
                    # the evaluated response text and the originating run.
                    "response": response,
                    "run_id": run_id,
                    "tenant_id": tenant_id,
                    "source": "judge",
                },
            )
            logger.info(
                f"Evaluation complete for {intent}: {result.quality.value} ({result.score:.2f})"
            )
            return result
        except Exception as e:
            record_failure("judge")
            logger.error(f"Evaluation failed: {e}")
            await self.event_bus.emit(
                EventNames.EVALUATION_FAILED, {"intent": intent, "error": str(e)}
            )
            return None

    # -- retention and read surface -------------------------------------------

    async def sweep_retention(self, now: float | None = None) -> int:
        """Delete stored verdicts older than ``EVAL_ONLINE_RETENTION_DAYS``.

        Args:
            now: Reference time (unix seconds); defaults to the current time.

        Returns:
            The number of records deleted (0 when retention is off).
        """
        config = self.config or get_evaluation_config()
        if self.store is None or config.online_retention_days <= 0:
            return 0
        cutoff = (now if now is not None else time.time()) - (
            config.online_retention_days * _DAY_SECONDS
        )
        try:
            removed = await self.store.purge_before(cutoff)
        except Exception as exc:
            record_failure("retention")
            logger.warning("online_eval_retention_failed: %s", exc)
            return 0
        if removed:
            logger.info("online_eval_retention_swept", extra={"removed": removed})
        return removed

    async def _sweep_loop(self) -> None:
        while True:
            await self.sweep_retention()
            await asyncio.sleep(RETENTION_SWEEP_INTERVAL_SECONDS)

    def summary(self) -> dict[str, Any]:
        """Sampling settings, queue depth and per-intent window statistics."""
        return {
            "running": self._running,
            "sample_rate": self._sampler.rate,
            "judge_rate": self._sampler.judge_rate,
            "queue_depth": self._queue.qsize() if self._queue is not None else 0,
            "intents": self.monitor.summary() if self.monitor is not None else [],
        }
