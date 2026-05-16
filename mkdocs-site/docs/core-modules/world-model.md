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

### Public API

| Symbol | Purpose |
|--------|---------|
| `IntentMandate` | User-signed spend envelope |
| `CartMandate` | Merchant-signed cart pinned to an intent |
| `CartItem` | Single line on a cart |
| `SignedMandate` | Mandate + detached Ed25519 signature |
| `sign_intent`, `sign_cart` | Build a `SignedMandate` from a private key |
| `verify_signature` | Verify one signature in isolation |
| `verify_chain` | Verify both signatures + enforce chain rules |
| `MandateError`, `MandateSignatureError`, `MandateChainError` | Error taxonomy |

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
