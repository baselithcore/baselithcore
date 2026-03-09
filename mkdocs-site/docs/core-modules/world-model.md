---
title: World Model
description: Internal representation and prediction of world state
---

**Module**: `core/world_model/`

The World Model provides agents with an internal representation of the environments they interact with. Instead of reacting statelessly to inputs, agents use the World Model to maintain an understanding of entities, relationships, and likely future states.

---

## Capabilities

The World Model acts as an in-memory graph-like representation during complex reasoning tasks, allowing the system to:

1. Track entities and their attributes over time.
2. Query the current state of tracked objects.
3. Simulate and predict the outcome of actions before executing them.

---

## Usage

```python
from core.world_model import WorldModel, Entity

model = WorldModel()

# 1. Add entities to the model
model.add_entity(Entity("user", attributes={"name": "Alice", "role": "admin"}))
model.add_entity(Entity("server", attributes={"status": "running", "load": 0.7}))

# 2. Query the current state
users = model.query("entities WHERE type = 'user'")

# 3. Update the state based on new observations
model.update("server", {"load": 0.9})

# 4. Predict the impact of a future action
prediction = await model.predict_next_state(action="deploy_new_version")
if prediction.risk_level == "high":
    log.warning("Predicted high risk for this deployment.")
```

## Integration with Reasoning

The World Model is tightly integrated with the Tree-of-Thoughts (`core/reasoning/`) and MCTS (Monte Carlo Tree Search) logic. By predicting internal states, agents can explore different branches of thought and evaluate the "simulated consequences" of actions without executing them in the real world.
