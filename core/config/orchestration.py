"""Orchestration and routing configuration (``ORCHESTRATOR_``, ``ROUTER_``).

Loop budgets, checkpointing and tool rate limiting for the agentic loop, plus
the intent router that picks the handler for a request.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings


class RouterConfig(BaseSettings):
    """Configuration for the Router."""

    model_config = {"env_prefix": "ROUTER_"}

    score_threshold: float = Field(
        default=0.7, description="Minimum similarity score for tool retrieval"
    )
    max_candidates: int = Field(
        default=5, description="Maximum number of agents to return"
    )
    retrieval_limit: int = Field(
        default=50,
        description="Number of entities to retrieve from vector store (N >> K)",
    )


class OrchestrationConfig(BaseSettings):
    """Configuration for the Orchestrator."""

    model_config = {"env_prefix": "ORCHESTRATOR_"}

    default_intent: str = Field(
        default="qa_docs", description="Default intent when classification fails"
    )
    enable_telemetry: bool = Field(
        default=False, description="Whether to record telemetry metrics"
    )
    confidence_threshold: float = Field(
        default=0.6, description="Minimum confidence for LLM classification"
    )

    # == Agent-loop runtime caps (defaults for ``LoopLimits``) ==
    # These are the production-safe ceilings every request inherits. Both can
    # be disabled with 0 for a deployment that really wants an unbounded loop,
    # and either can still be overridden per request by constructing
    # ``LoopLimits`` explicitly.
    loop_max_tokens: int = Field(
        default=400_000,
        ge=0,
        description="Cumulative token cap (input + output, every LLM call) for "
        "one orchestrated request. 0 disables the cap.",
    )
    loop_max_seconds: float = Field(
        default=600.0,
        ge=0,
        description="Wall-clock deadline in seconds for one orchestrated "
        "request; also shrinks per-tool/LLM timeouts so a single slow call "
        "cannot outlive it. 0 disables the deadline.",
    )
    context_window_tokens: int = Field(
        default=200_000,
        ge=0,
        description="Assumed model context window, used as the denominator of "
        "LoopBudget.token_pressure() when no token cap is set so context "
        "auto-tuning still has a signal. 0 disables that fallback.",
    )
    tool_ledger: Literal["auto", "postgres", "memory", "off"] = Field(
        default="auto",
        description="Backing store for the tool idempotency ledger, which "
        "keeps a redelivered or resumed run from repeating an effectful call. "
        "'auto' uses Postgres when POSTGRES_ENABLED, else the in-process "
        "ledger with a warning; 'postgres' requires it and fails loudly "
        "instead of silently deduplicating within one worker only; 'memory' "
        "always uses the in-process ledger (single-worker deployments and "
        "tests); 'off' records nothing, so every retry re-executes every "
        "effectful call.",
    )

    recovery_sweep_interval_seconds: float = Field(
        default=300.0,
        gt=0,
        description="Interval between background crash-recovery sweeps "
        "(resume interrupted runs + fail wedged ones) when "
        "checkpoint_resume_on_startup is enabled.",
    )
    recovery_stale_after_seconds: float = Field(
        default=1800.0,
        gt=0,
        description="Progress-silence threshold after which a 'running' "
        "checkpoint is marked failed by the stale-run sweep.",
    )
    recovery_resume_after_seconds: float = Field(
        default=300.0,
        gt=0,
        description="Minimum progress silence before a 'running' checkpoint is "
        "re-entered by the recovery sweep. Recent progress means some worker "
        "still owns the run, and resuming it anyway would execute the same "
        "agent loop twice. Keep it at or above the sweep interval.",
    )

    # == Durable checkpointing / human-in-the-loop ==
    # When enabled, the chat orchestrator is wired with a checkpoint store:
    # every run persists a resumable checkpoint, approval gates pause runs
    # durably (awaiting_approval) instead of failing terminally, and the
    # /approvals API (list / decide / resume) becomes available.
    # On by default: with the 'auto' backend this is Postgres-durable when
    # Postgres storage is enabled, else a bounded in-memory store (durable
    # HITL within the process; capped by checkpoint_memory_max_entries).
    checkpoint_enabled: bool = Field(
        default=True,
        description="Wire a checkpoint store into the chat orchestrator: runs "
        "persist resumable checkpoints, approval gates pause durably and the "
        "/approvals API is mounted.",
    )
    checkpoint_backend: str = Field(
        default="auto",
        description="Checkpoint store backend: 'postgres', 'sqlite', 'memory', "
        "or 'auto' (postgres when Postgres storage is enabled, else memory). "
        "'sqlite' gives durable runs from a single file, with no Postgres — for "
        "development, air-gapped or single-node deployments.",
    )
    checkpoint_sqlite_path: str = Field(
        default="data/checkpoints.db",
        description="Database file for the 'sqlite' checkpoint backend "
        "(durable runs without a Postgres instance; parent directories are "
        "created on first use).",
    )
    checkpoint_resume_on_startup: bool = Field(
        default=False,
        description="Start the background recovery sweeps: runs left in the "
        "'running' state by a crash/restart are re-entered, and runs that "
        "stopped making progress are marked failed. Sweeps repeat every "
        "recovery_sweep_interval_seconds (not just at startup); a run is only "
        "re-entered once it has been silent for "
        "recovery_resume_after_seconds, so one still executing is left alone. "
        "Requires checkpoint_enabled; runs awaiting approval are never "
        "auto-resumed.",
    )
    checkpoint_history_enabled: bool = Field(
        default=False,
        description="Also append an immutable snapshot of the checkpoint at "
        "every version (time-travel / state history; requires "
        "checkpoint_enabled).",
    )
    checkpoint_history_limit: int = Field(
        default=200,
        description="Per-run cap on retained history snapshots (newest kept); "
        "0 means unlimited.",
    )
    tool_rate_limit_enabled: bool = Field(
        default=False,
        description="Enforce a sliding-window burst limit on side-effecting "
        "tool invocations (categories destructive/external_side_effect), "
        "keyed (tenant, tool). In-process; off by default.",
    )
    tool_rate_limit_max_calls: int = Field(
        default=30,
        ge=1,
        description="Invocations allowed per (tenant, tool) inside one window.",
    )
    tool_rate_limit_window_seconds: float = Field(
        default=60.0,
        gt=0,
        description="Sliding-window length in seconds for the tool rate limit.",
    )
    checkpoint_memory_max_entries: int = Field(
        default=1000,
        description="Retained-run cap for the in-memory checkpoint backend "
        "(oldest finished runs evicted first). Irrelevant for the Postgres "
        "backend.",
    )
    hitl_callback_threads: int = Field(
        default=8,
        ge=1,
        description="Worker threads for blocking human-in-the-loop callbacks. "
        "They run on a dedicated pool, never the interpreter default one: a "
        "callback waits on a person, and a timed-out one never returns its "
        "thread, so sharing would starve short latency-critical tasks.",
    )
    crew_max_parallel: int = Field(
        default=8,
        ge=1,
        description="Maximum crew tasks executed concurrently under "
        "process='parallel'. Each task is a full LLM call, so an unbounded "
        "fan-out over a caller-supplied task list would open that many "
        "simultaneous provider calls (429 storm + unmetered cost spike).",
    )


_router_config: RouterConfig | None = None
_orchestration_config: OrchestrationConfig | None = None


def get_router_config() -> RouterConfig:
    """Get router configuration."""
    global _router_config
    if _router_config is None:
        _router_config = RouterConfig()
    return _router_config


def get_orchestration_config() -> OrchestrationConfig:
    """Get orchestration configuration."""
    global _orchestration_config
    if _orchestration_config is None:
        _orchestration_config = OrchestrationConfig()
    return _orchestration_config
