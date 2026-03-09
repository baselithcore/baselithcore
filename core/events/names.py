"""
Predefined Event Names.

Standard event names used throughout the baselith-core
for consistency and discoverability.
"""


class EventNames:
    """Standard event names used in the system."""

    # Agent lifecycle
    AGENT_STARTING = "agent.starting"
    AGENT_STARTED = "agent.started"
    AGENT_READY = "agent.ready"
    AGENT_STOPPING = "agent.stopping"
    AGENT_STOPPED = "agent.stopped"
    AGENT_COMPLETED = "agent.completed"
    AGENT_FAILED = "agent.failed"
    AGENT_RECOVERED = "agent.recovered"

    # Task lifecycle
    TASK_CREATED = "task.created"
    TASK_STARTED = "task.started"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"

    # Orchestration
    FLOW_STARTED = "flow.started"
    FLOW_STEP_COMPLETED = "flow.step_completed"
    FLOW_COMPLETED = "flow.completed"

    # Learning Events
    EXPERIENCE_RECORDED = "learning.experience_recorded"
    LEARNING_UPDATED = "learning.updated"

    # Evaluation Events
    EVALUATION_STARTED = "evaluation.started"
    EVALUATION_COMPLETED = "evaluation.completed"
    EVALUATION_FAILED = "evaluation.failed"

    # System
    SYSTEM_READY = "system.ready"
    SYSTEM_SHUTDOWN = "system.shutdown"

    # Plugin
    PLUGIN_LOADED = "plugin.loaded"
    PLUGIN_UNLOADED = "plugin.unloaded"

    # Optimization
    OPTIMIZATION_COMPLETED = "optimization.completed"

    # Fine-Tuning
    FINETUNING_TRIGGERED = "finetuning.triggered"
    FINETUNING_STARTED = "finetuning.started"
    FINETUNING_COMPLETED = "finetuning.completed"
    FINETUNING_FAILED = "finetuning.failed"


__all__ = ["EventNames"]
