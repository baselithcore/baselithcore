---
title: Learning Loop & Evolution
description: Continuous learning and evolutionary improvement of prompts
---

**Modules**: `core/learning/` and `core/learning/evolution.py`

The framework includes autonomous mechanisms for continuous learning from past interactions. Rather than relying on static system prompts, agents can evolve their strategies over time using genetic algorithms and reinforcement learning concepts.

---

## Module Structure

```text
core/learning/
├── __init__.py           # Public exports
├── learning_loop.py      # ContinuousLearner and LearningLoop
├── evolution.py          # EvolutionService
├── experience_buffer.py  # ExperienceReplay (PER)
├── reward_model.py       # RewardModel implementation
└── policy_optimizer.py   # PolicyOptimizer (PPO/DPO)
```

---

## Continuous Learning

The `ContinuousLearner` manages the learning lifecycle, interacting with the `ExperienceReplay` buffer and `RewardModel`.

```python
from core.learning.learning_loop import ContinuousLearner

learner = ContinuousLearner()

# 1. Record an experience during an episode
await learner.record_experience(
    query=query,
    response=response,
    score=feedback_score
)

# 2. Trigger training when enough data is collected
if await learner.train():
    log.info("Learning loop iteration completed successfully.")
```

## Evolutionary Optimization

The `EvolutionService` applies evolutionary algorithms to populations of prompts or strategies, scoring them and "breeding" the best performers to create a new, optimized generation.

```python
from core.learning import EvolutionService

evolution = EvolutionService()

# Evaluate how well different prompt variations perform
scores = await evolution.evaluate_population(prompts)

# Evolve the prompts toward better performance
new_generation = await evolution.evolve(
    population=prompts,
    scores=scores,
    mutation_rate=0.1  # Degree of variance in the next generation
)
```

This evolution is often triggered automatically by the Optimization Loop when the `EvaluationService` reports degrading agent performance.
