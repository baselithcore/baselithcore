---
title: World Model
description: Internal representation and prediction of world state
---

**Module**: `core/world_model/`

The World Model gives agents predictive planning: a `State` captures the current
context as variables, an `Action` declares effects/preconditions/cost/risk, and
the subsystem can predict next states, simulate action paths via MCTS, score
risk, and plan rollbacks — all before anything executes in the real world.

There is **no** `WorldModel` or `Entity` class; the public surface is a set of
data types plus four service classes.

---

## Public API

`core.world_model` exports:

```python
from core.world_model import (
    State, Action, Transition, SimulationResult, RiskLevel,  # types
    StatePredictor,    # predict next state from (state, action)
    MCTSSimulator,     # Monte Carlo Tree Search over action paths
    RiskAssessor,      # score action / path risk
    RollbackPlanner,   # plan inverse actions
)
```

| Symbol | Kind | Notes |
|--------|------|-------|
| `State` | dataclass | `variables` dict; `.get`, `.set` (returns new state), `.copy`, `.diff` |
| `Action` | dataclass | `name`, `action_type`, `effects`, `preconditions`, `cost`, `risk_level`, `reversible`; `.can_apply`, `.apply` |
| `Transition` | dataclass | `source_state`, `action`, `target_state`, `probability`, `reward` |
| `SimulationResult` | dataclass | `best_path`, `all_paths`, `goal_reached`; `.best_reward`, `.explored_paths` |
| `RiskLevel` | Enum | `MINIMAL` / `LOW` / `MEDIUM` / `HIGH` / `CRITICAL` |

---

## Usage

```python
from core.world_model import (
    State, Action, StatePredictor, RiskAssessor, RollbackPlanner,
)
from core.world_model.types import ActionType

# 1. Describe the current context as a State
state = State(name="deploy", variables={"version": "1.0", "load": 0.7})

# 2. Define an Action with explicit effects
deploy = Action(
    name="deploy_new_version",
    action_type=ActionType.UPDATE,
    effects={"version": "1.1"},
    reversible=True,
)

# 3. Predict the resulting state (async; uses action effects, or an LLM)
predictor = StatePredictor()
next_state = await predictor.predict(state, deploy)
print(next_state.get("version"))  # -> "1.1"

# 4. Assess risk before committing (sync; returns a dict with score + level)
assessor = RiskAssessor()
risk = assessor.assess_action(deploy, state)
if risk["level"].value >= 4:  # RiskLevel.HIGH
    log.warning("Predicted high risk for this deployment.")

# 5. Plan a rollback
planner = RollbackPlanner()
planner.record_action(deploy, state)
rollback = planner.create_rollback(deploy, checkpoint_state=state)
if rollback.can_rollback:
    log.info("Rollback is feasible.")
```

For multi-step lookahead, `MCTSSimulator` runs Monte Carlo Tree Search over
candidate action paths (`await simulator.search(...)` returns a
`SimulationResult`), and `StatePredictor.predict_sequence(...)` chains
predictions across a list of actions.

## Integration with Reasoning

The World Model is tightly integrated with the Tree-of-Thoughts (`core/reasoning/`) and MCTS (Monte Carlo Tree Search) logic. By predicting internal states, agents can explore different branches of thought and evaluate the "simulated consequences" of actions without executing them in the real world.

---

## Mandate Chain for Agent-Initiated Commerce

`core/world_model/mandates.py` implements a signed mandate chain so an
agent can never spend more than the user explicitly authorized. Every
purchase requires:

1. An **`IntentMandate`** signed by the user (Ed25519). It states the
   item description, a `max_price_usd` ceiling, expiration, and free-form
   conditions.
2. A **`CartMandate`** signed by the merchant. It pins back to the
   intent via `intent_id` and lists the actual line items.

`verify_chain(...)` walks both signatures, checks the intent has not
expired, refuses any cart whose `intent_id` differs from the intent,
and refuses any cart whose total exceeds `intent.max_price_usd`.
Tampering with the cart after signing invalidates the signature.

### Replay protection

A valid signed chain is otherwise reusable for the whole intent lifetime —
nothing stops the same authorized purchase from being submitted twice. Pass an
optional `replay_guard` to consume each intent exactly once:

```python
from core.world_model.mandates import InMemoryReplayGuard, MandateReplayError

guard = InMemoryReplayGuard()  # production: back this with Redis SET NX
verify_chain(signed_intent, signed_cart, user_public_key=..., merchant_public_key=..., replay_guard=guard)
# Second attempt with the same intent_id raises MandateReplayError.
```

- **Backwards compatible**: omit `replay_guard` and behavior is unchanged
  (stateless, no replay protection).
- **Keyed on `intent_id`**: one signed intent authorizes exactly one purchase.
- **Consumed only after every other check passes**, so a rejected chain never
  burns a legitimate intent.
- `ReplayGuard` is a `Protocol` (one atomic `register_once(key) -> bool`).
  `InMemoryReplayGuard` is process-local; supply a Redis-backed implementation
  across workers/restarts.

### Public API

| Symbol | Purpose |
|--------|---------|
| `IntentMandate` | User-signed spend envelope |
| `CartMandate` | Merchant-signed cart pinned to an intent |
| `CartItem` | Single line on a cart |
| `SignedMandate` | Mandate + detached Ed25519 signature |
| `sign_intent`, `sign_cart` | Build a `SignedMandate` from a private key |
| `verify_signature` | Verify one signature in isolation |
| `verify_chain` | Verify both signatures + enforce chain rules (optional `replay_guard`) |
| `ReplayGuard`, `InMemoryReplayGuard` | Single-use ledger protocol + in-memory impl |
| `MandateError`, `MandateSignatureError`, `MandateChainError`, `MandateReplayError` | Error taxonomy |

### Example

```python
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from core.world_model.mandates import (
    CartItem, CartMandate, IntentMandate,
    new_intent_id, new_cart_id,
    sign_intent, sign_cart, verify_chain,
)

user_key = Ed25519PrivateKey.generate()
merchant_key = Ed25519PrivateKey.generate()

intent = IntentMandate(
    intent_id=new_intent_id(),
    user_id="user-1",
    item_description="laptop",
    max_price_usd=1500.0,
    expires_at=time.time() + 3600,
)
signed_intent = sign_intent(intent, user_key)

cart = CartMandate(
    cart_id=new_cart_id(),
    intent_id=intent.intent_id,
    merchant_id="merchant-1",
    items=[CartItem(sku="LP-001", quantity=1, unit_price_usd=1399.0)],
)
signed_cart = sign_cart(cart, merchant_key)

# Raises if signatures are invalid, cart over-budget, or intent expired.
verify_chain(
    signed_intent,
    signed_cart,
    user_public_key=user_key.public_key(),
    merchant_public_key=merchant_key.public_key(),
)
```

!!! warning "Key custody is out of scope"
    The module owns the protocol but not key storage. Source
    `Ed25519PrivateKey` material from your secrets backend as a
    `pydantic.SecretStr`-wrapped value before signing.
