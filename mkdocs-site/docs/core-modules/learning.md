---
title: Learning Loop & Evolution
description: Continuous reinforcement learning and evaluation-driven self-improvement
---

**Module**: `core/learning/`

The framework includes autonomous mechanisms for continuous learning from past
interactions. Agents collect experiences, score them with a reward model, and
periodically optimize their action-selection policy. A separate
`EvolutionService` reacts to evaluation events to refine memory and trigger
automatic fine-tuning.

---

## Module Structure

```text
core/learning/
├── __init__.py            # Public exports
├── learning_loop.py       # ContinuousLearner, PersistentLearner
├── evolution.py           # EvolutionService
├── auto_finetuning.py     # AutoFineTuningService, AutoFineTuneConfig
├── feedback.py            # FeedbackCollector, FeedbackItem
├── experience_buffer.py   # ExperienceReplay (prioritized replay)
├── reward_model.py        # RewardModel
├── policy_optimizer.py    # PolicyOptimizer
└── types.py               # Experience, Episode, LearningPhase
```

Public exports from `core.learning`:

```python
from core.learning import (
    FeedbackCollector,
    FeedbackItem,
    ContinuousLearner,
    PersistentLearner,
    EvolutionService,
    AutoFineTuningService,
    AutoFineTuneConfig,
)
```

`ExperienceReplay`, `RewardModel`, `PolicyOptimizer` and the `types` are
internal building blocks — import them from their submodules if needed.

---

## Continuous Learning

`ContinuousLearner` manages the improvement cycle: it wraps an
`ExperienceReplay` buffer, a `RewardModel`, and a `PolicyOptimizer`. Experiences
are recorded, automatically scored, and training fires every
`training_interval` experiences.

```python
from core.learning import ContinuousLearner

learner = ContinuousLearner(
    buffer_capacity=10000,
    training_interval=100,
    batch_size=32,
    exploration_rate=0.1,
)

# Optionally scope experiences to an episode
learner.start_episode(context={"task": "support_triage"})

# Record an experience (synchronous)
exp = learner.record_experience(
    state={"query": "reset password"},
    action="send_reset_link",
    outcome="user confirmed reset",
    success=True,
    next_state={"status": "resolved"},
)

learner.end_episode(success=True)

# Select an action under the learned policy
action = learner.select_action(
    state={"query": "billing question"},
    available_actions=["escalate", "answer_inline", "open_ticket"],
)

# Trigger training explicitly (synchronous; returns a stats dict)
stats = learner.train(iterations=10)
print(stats)
print(learner.get_stats())
```

Key methods (all synchronous):

| Method | Purpose |
|--------|---------|
| `record_experience(state, action, outcome, success=False, next_state=None, metadata=None)` | Store and auto-score an `Experience` |
| `select_action(state, available_actions)` | Pick an action via the policy optimizer |
| `train(iterations=10)` | Run training, returns a stats dict |
| `start_episode(context=None)` / `end_episode(success=False)` | Episode lifecycle |
| `provide_feedback(experience_id, human_reward)` | Inject human reward |
| `import_demonstrations(demonstrations)` | Behavior cloning from expert demos |
| `get_best_actions(state, actions, top_k=3)` | Top-K `(action, value)` tuples |
| `get_stats()` / `save_state()` / `load_state(state)` | Introspection and persistence |

### Persistent Learner

`PersistentLearner` subclasses `ContinuousLearner` and adds Redis-backed
checkpointing. State auto-loads on init and auto-saves after training.

```python
from core.learning import PersistentLearner

learner = PersistentLearner(learner_id="support_agent")  # loads prior state
learner.record_experience(state, action, outcome)
learner.train()          # auto-saves to Redis (every checkpoint_interval runs)
learner.checkpoint()     # manual save
learner.reset_state()    # clear local + Redis state
```

Constructor: `PersistentLearner(learner_id="default", auto_save=True,
auto_load=True, checkpoint_interval=1, **kwargs)` — extra kwargs pass through
to `ContinuousLearner`.

---

## Evolution Service

`EvolutionService` is **not** a genetic prompt-breeder. It is an
evaluation-event-driven controller: it subscribes to `EVALUATION_COMPLETED`
events and reacts by writing memory entries (lessons learned / best practices)
and optionally driving automatic fine-tuning.

```python
from core.learning import EvolutionService
from core.memory import AgentMemory

evolution = EvolutionService(
    memory_manager=AgentMemory(),     # optional
    enable_auto_finetuning=True,
)

evolution.start()   # subscribes to evaluation events; starts AutoFineTuningService

# Inspect metrics
print(evolution.get_evolution_stats())

# Manually drive fine-tuning through the embedded service
job_id = await evolution.trigger_manual_finetuning()

evolution.stop()
```

Behavior on each `EVALUATION_COMPLETED` event (when a `memory_manager` is set):

- `score < 0.4` → stores a "Lesson Learned" memory entry.
- `score > 0.9` → stores a "Best Practice" memory entry.

Public API: `start()`, `stop()`, `get_evolution_stats()`,
`trigger_manual_finetuning()` (async), and the lazy `auto_finetuning_service`
property. See [Auto Fine-Tuning](finetuning.md) for the downstream pipeline.
