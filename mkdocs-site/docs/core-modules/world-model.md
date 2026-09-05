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
rollback = planner.create_rollback(deploy, state_before=state)
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
   item description, a `max_price_usd` ceiling, expiration, and
   `conditions` — signed constraints the verifier enforces (see
   [Signed intent conditions](#signed-intent-conditions)).
2. A **`CartMandate`** signed by the merchant. It pins back to the
   intent via `intent_id` and lists the actual line items.

`verify_chain(...)` walks both signatures, checks the intent has not
expired, refuses any cart whose `intent_id` differs from the intent,
refuses any cart whose total exceeds `intent.max_price_usd`, and enforces
the intent's signed conditions. Tampering with the cart after signing
invalidates the signature.

Two timestamp rules are always on, whatever the caller passes. A cart dated in
the future is refused, and a cart that predates its own intent is refused — the
merchant signs the cart *against* the intent, so it cannot legitimately come
first; either shape indicates a replayed or forged envelope. Both comparisons
allow `_CLOCK_SKEW_TOLERANCE_SECONDS = 60.0` of drift, wide enough that
ordinary NTP skew between the user's signer and the merchant's never rejects a
legitimate chain, narrow enough to stay meaningful.

### Money math in integer cents

All money *comparisons* run in integer cents, never accumulated floats.
`CartItem.line_total_cents()` and `CartMandate.total_cents()` are the exact
values (`line_total()` / `total_usd()` are derived from them by dividing by
100), and `verify_chain` compares `cart.total_cents()` against
`intent.max_price_usd` converted via `Decimal(str(value))` with half-up
rounding. This closes both float failure modes at once:

- Three items at `0.10` no longer *exceed* a `0.30` cap
  (`0.1 + 0.1 + 0.1 > 0.3` in binary floats) — a legitimate at-cap cart
  verifies.
- No cart marginally *above* the cap can slip under it through float noise.

The **wire and signature format is unchanged**: the canonical payload keeps
the float fields, so existing signatures stay valid. `_canonical_bytes`
documents that float fields serialize via Python `repr` semantics — a
cross-language verifier must reproduce the same float formatting to compute
identical bytes; only the comparisons are float-immune.

### Binding the cart to a merchant and a freshness window

`verify_chain` takes two optional keyword arguments, both `None` (off) by
default, that constrain what a valid signature is allowed to claim:

| Argument | Effect | Why the signature does not already imply it |
|----------|--------|---------------------------------------------|
| `expected_merchant_id` | `cart.merchant_id` must equal it, else `MandateChainError` | `merchant_public_key` alone does not bind the cart to a merchant: a caller holding one trusted key would otherwise accept a cart *claiming* any `merchant_id`, as long as that key signed it |
| `max_cart_age_seconds` | Reject a cart whose `issued_at` is older than this | `IntentMandate` carries `expires_at`, but `CartMandate` has no expiry of its own — without this a cart stays verifiable for the whole intent window, long after the quoted prices stopped being current |

```python
verify_chain(
    signed_intent,
    signed_cart,
    user_public_key=user_key.public_key(),
    merchant_public_key=merchant_key.public_key(),
    expected_merchant_id="merchant-1",
    max_cart_age_seconds=300,     # quoted prices go stale after 5 minutes
)
```

Both are backwards compatible: omit them and verification behaves exactly as
before. A caller that resolves `merchant_public_key` **from**
`cart.merchant_id` already gets the binding implicitly and can leave
`expected_merchant_id` unset. The value is peer-supplied, so it goes through
`sanitize_log_value` before it reaches the rejection message — a crafted id
cannot forge extra log lines downstream.

### Signed intent conditions

!!! danger "Security fix"
    Earlier releases *carried* `IntentMandate.conditions` in the signed
    envelope but never evaluated them — the user signed a constraint the
    verifier silently ignored, widening the authorization beyond what was
    granted. `verify_chain` now enforces them, **on by default**.

The user signs `conditions` into the intent envelope, so the verifier must
either enforce a condition or reject the chain
(`core/world_model/mandate_conditions.py`). Supported conditions:

| Condition | Type | Enforcement |
|-----------|------|-------------|
| `allowed_merchants` | `list[str]` | `cart.merchant_id` must be listed |
| `max_items` | `int` | Total cart quantity across all lines must not exceed it |
| `allowed_skus` | `list[str]` | Every cart line's SKU must be listed |

**Unknown condition keys fail closed**: a signed constraint this verifier
cannot evaluate is reported as a violation, never skipped — otherwise an
unrecognized key would silently widen the authorization. Malformed values
(wrong type, `max_items < 1`) are likewise violations. Any violation raises
`MandateChainError` listing every failed condition; peer-supplied values
(merchant ids, SKUs) pass through `sanitize_log_value` first.

Enforcement runs **before replay consumption**, so a rejected cart never
burns the intent — a conforming cart can still be verified afterwards.
Kill-switch: `BASELITH_AP2_ENFORCE_CONDITIONS=0` restores the pre-fix
behavior.

```python
intent = IntentMandate(
    intent_id=new_intent_id(),
    user_id="user-1",
    item_description="laptop",
    max_price_usd=1500.0,
    expires_at=time.time() + 3600,
    conditions={"allowed_merchants": ["merchant-1"], "max_items": 3},
)
# A cart signed by another merchant, or holding more than 3 items, now
# raises MandateChainError — without consuming the intent.
```

### Replay protection

A valid signed chain is otherwise reusable for the whole intent lifetime —
nothing stops the same authorized purchase from being submitted twice.
`verify_chain(...)` therefore consumes each intent exactly once through a
`replay_guard`, **on by default**:

```python
from core.world_model.mandates import MandateReplayError, verify_chain

verify_chain(
    signed_intent,
    signed_cart,
    user_public_key=user_key.public_key(),
    merchant_public_key=merchant_key.public_key(),
)
# Second attempt with the same intent_id raises MandateReplayError.
```

- **Keyed on `intent_id`**: one signed intent authorizes exactly one purchase.
- **Consumed only after every other check passes**, so a rejected chain never
  burns a legitimate intent.
- `ReplayGuard` is a `Protocol` (one atomic `register_once(key) -> bool`).
  Pass your own implementation to override the default, or `replay_guard=None`
  to opt into stateless verification with no replay protection.

### Shared (cross-worker) replay ledger

`InMemoryReplayGuard` is process-local. With `WEB_CONCURRENCY > 1` — the normal
production shape — every worker keeps its own set, so the same signed chain
verifies once *per worker*: replay protection quietly becomes "N executions of
one authorized purchase". `core/world_model/replay_guard.py` supplies the
shared ledger and resolves the default:

| Symbol | Purpose |
|--------|---------|
| `RedisReplayGuard` | Cross-worker guard: one atomic `SET key value NX EX` per intent, so two workers racing on the same `intent_id` can never both see "first use" |
| `ReplayLedgerUnavailableError` | Raised (not returned) when the ledger is unreachable |
| `build_default_replay_guard()` | Picks the strongest guard the deployment supports |

`build_default_replay_guard()` returns a `RedisReplayGuard` when
`CACHE_REDIS_URL` is configured (default `redis://localhost:6379/1`), and
otherwise falls back to `InMemoryReplayGuard` — logging an ERROR in production,
because a process-local ledger there is the per-worker degradation above.
`verify_chain` resolves this default **lazily on first use** and memoizes it, so
importing `core.world_model.mandates` never touches config or Redis; passing an
explicit guard, or `None`, bypasses the resolver entirely.

The client is injected, so the caller owns the connection lifecycle. It must be
a **synchronous** `redis.Redis` — `verify_chain` is a sync function:

```python
from redis import Redis

from core.world_model.mandates import verify_chain
from core.world_model.replay_guard import RedisReplayGuard

guard = RedisReplayGuard(
    Redis.from_url("redis://cache:6379/1"),
    ttl_seconds=7 * 24 * 3600,          # default: 7 days
    key_prefix="baselith:ap2:intent:",  # default
)
verify_chain(
    signed_intent,
    signed_cart,
    user_public_key=user_key.public_key(),
    merchant_public_key=merchant_key.public_key(),
    replay_guard=guard,
)
```

A consumed intent id only has to outlive the intent's own expiry window —
`verify_chain` already rejects an expired intent, so a longer-lived key is
redundant, never wrong. The seven-day default covers any sane window while
keeping the keyspace bounded.

!!! warning "Fail-closed: an unreachable ledger refuses the purchase"
    If the Redis round trip raises, `RedisReplayGuard.register_once` raises
    `ReplayLedgerUnavailableError` instead of answering. A guard that cannot
    prove an intent is unused must not report it as unused: for a payment
    authorization, *unknown* has to read as *refused*, never as *first use*.
    Callers verifying a chain should expect this alongside the `MandateError`
    taxonomy and surface it as a temporary failure — an availability blip is
    cheaper than a double charge.

`InMemoryReplayGuard` is also **bounded**: `max_entries` (default
`_MAX_REMEMBERED_INTENTS = 100_000`) caps the consumed-intent ledger, dropping
the oldest ids first — the backing map is a plain insertion-ordered `dict`, so
FIFO eviction is cheap. A ledger that only ever grows is a slow memory leak in
a long-lived process. Eviction does mean a *very* old intent could in principle
be replayed, a limit mitigated by the intent's own expiry, which `verify_chain`
enforces independently. `RedisReplayGuard` has no such trade-off: its entries
expire by TTL.

### Public API

| Symbol | Purpose |
|--------|---------|
| `IntentMandate` | User-signed spend envelope |
| `CartMandate` | Merchant-signed cart pinned to an intent (`total_cents()` exact, `total_usd()` derived) |
| `CartItem` | Single line on a cart (`line_total_cents()` exact, `line_total()` derived) |
| `SignedMandate` | Mandate + detached Ed25519 signature (`signature_hex`) |
| `sign_intent`, `sign_cart` | Build a `SignedMandate` from a private key |
| `verify_signature` | Verify one signature in isolation |
| `verify_chain` | Verify both signatures + enforce chain rules (replay-guarded by default; signed intent conditions enforced, unknown keys fail closed; optional `expected_merchant_id` / `max_cart_age_seconds` binding) |
| `ReplayGuard`, `InMemoryReplayGuard` | Single-use ledger protocol + process-local impl (bounded, FIFO eviction) |
| `MandateError`, `MandateSignatureError`, `MandateChainError`, `MandateReplayError` | Error taxonomy |

`SignedMandate.signature_hex` arrives from an untrusted peer, so decoding it is
part of verification: `SignedMandate.signature` raises `MandateSignatureError`
on a non-hex value rather than letting a `ValueError` escape the verification
boundary (which a caller would surface as a `500` instead of a rejection).

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

# Raises if signatures are invalid, cart over-budget, intent expired,
# cart dated before its intent or in the future, a signed condition
# violated, or the intent was already consumed (replay).
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

---

## AP2 Payment Execution (`execute_payment`)

`core/world_model/mandates.py` owns the *protocol* — signatures, the
cart-vs-intent envelope, replay consumption — and explicitly leaves
"execution of the purchase" elsewhere. `core/world_model/payments.py` is
that elsewhere: `execute_payment` is the **single chokepoint** between a
verified mandate chain and a payment service provider (PSP). The
orchestration is strictly ordered:

1. **`verify_chain` runs first** with the exact arguments given — consuming
   the intent through the replay guard, so a chain executes **at most
   once**. A rejected chain propagates its `MandateError` unchanged: the
   executor is never called and no receipt is produced.
2. The verified cart is handed to a **`PaymentExecutor`** — a PSP adapter
   living in a plugin. Executors receive the *already-verified*
   `CartMandate` plus an opaque `payment_method_ref` token; they never see
   mandate keys, signatures, or raw payment credentials.
3. The resulting **`PaymentReceipt`** is recorded in the `ReceiptStore`
   (when one is given) and a `payment.executed` / `payment.failed`
   [audit event](audit-trail.md) is emitted for every outcome.

A *declined* charge is a `"declined"` receipt, not an exception —
exceptions are reserved for infrastructure failures. When the executor
itself raises, `execute_payment` records an `"error"` receipt (when a store
is available), emits `payment.failed`, and raises `PaymentExecutionError`
with the original exception chained as `__cause__`. Note the asymmetry:
the chain verified, so the intent **was consumed** even though the charge
failed.

```python
from core.world_model import InMemoryReceiptStore, execute_payment
from plugins.payments.mock_psp import MockPSPAdapter

store = InMemoryReceiptStore()
receipt = await execute_payment(
    signed_intent,
    signed_cart,
    user_public_key=user_key.public_key(),
    merchant_public_key=merchant_key.public_key(),
    executor=MockPSPAdapter(),
    payment_method_ref="pm_tok_abc123",   # opaque token, never a PAN
    receipt_store=store,
)
receipt.status          # "captured" | "declined"
receipt.amount_cents    # integer cents, like all AP2 money math
await store.list_for_intent(receipt.intent_id)
```

`expected_merchant_id`, `max_cart_age_seconds` and `replay_guard` pass
through to `verify_chain` unchanged; the guard defaults to the deployment's
default single-use ledger.

### Receipts are non-repudiation evidence

`PaymentReceipt` is a frozen record of one payment attempt:
`transaction_id`, `intent_id`, `cart_id`, `merchant_id`, `amount_cents`,
`status` (`"captured"` / `"declined"` / `"error"`), `executed_at`, `psp`,
`currency` (ISO 4217; the AP2 chain runs in USD today) and `detail`.
`ReceiptStore` is an append-only protocol (`record` / `get` /
`list_for_intent`); `record` must be idempotent per `transaction_id` —
first write wins, re-recording never rewrites history.
`InMemoryReceiptStore` is the process-local implementation for tests and
single-process runs; it does not survive restarts, so production
deployments should persist receipts durably.

### The PSP seam lives in a plugin

PSP adapters are external integrations, so per the Sacred Core rule they
implement the `PaymentExecutor` protocol from **outside** `core/`. The
reference adapter is `plugins/payments` (signed manifest):
`MockPSPAdapter` captures every charge at or under an optional
`decline_over_cents` threshold and declines anything above it — the shape
a real Stripe/Adyen adapter should follow (credentials in
`pydantic.SecretStr`, declines as receipts, exceptions only for
infrastructure failures).

### Delegated purchases (human-not-present)

`core/world_model/delegated.py` adds the autonomous-buyer mode: the user
pre-signs one or more intents while present, and a
`DelegatedPurchaseAgent` later evaluates incoming merchant offers with
nobody watching.

```python
from core.world_model import DelegatedPurchaseAgent

agent = DelegatedPurchaseAgent(
    executor=psp_adapter,
    user_public_key_resolver=lambda user_id: key_directory[user_id],
    receipt_store=store,
    max_intents=1000,        # bounded registry (default)
)
agent.register(signed_intent)          # signature verified immediately

receipt = await agent.evaluate_offer(
    signed_cart,
    merchant_public_key=merchant_key.public_key(),
    payment_method_ref="pm_tok_abc123",
)
# receipt is None (offer passed over, zero side effects) or a
# PaymentReceipt (which may still be "declined").
```

- **Registration verifies immediately.** The user signature is checked
  against the resolver's key for `intent.user_id` on `register` — an
  invalid or expired envelope never enters the registry. The resolver is
  called again at execution time, so trust decisions never outlive a key
  rotation. The registry is bounded (`max_intents`, default `1000`);
  registering past it prunes expired entries first, then raises
  `DelegationRegistryFullError`.
- **Pre-flights are side-effect-free.** `evaluate_offer` returns `None` —
  consuming nothing — when the cart references no registered intent, the
  intent has expired, the total exceeds the authorized cap, or the cart
  violates the intent's signed conditions. Only an offer that passes every
  pre-flight is delegated to `execute_payment`, which re-verifies the full
  chain and consumes the intent: the agent has **no path around the
  protocol**.
- **One intent = max one purchase.** The replay guard enforces it; a
  replayed intent resolves to `None` rather than a second charge, and a
  captured purchase drops the registry entry.

!!! danger "Signed conditions are unconditional in delegated mode"
    Present-mode verification honours the
    `BASELITH_AP2_ENFORCE_CONDITIONS` kill-switch (see
    [Signed intent conditions](#signed-intent-conditions)).
    `DelegatedPurchaseAgent` ignores it: with no human present, the signed
    conditions are **always** enforced — there is nobody around to notice
    a silently widened authorization.

### Payment API

| Symbol | Purpose |
|--------|---------|
| `execute_payment` | Verify chain → execute charge → record receipt → audit (the single chokepoint) |
| `PaymentExecutor` | PSP protocol: one async `execute(cart, *, payment_method_ref) -> PaymentReceipt` |
| `PaymentReceipt`, `PaymentStatus` | Frozen non-repudiation record; `"captured"` / `"declined"` / `"error"` |
| `ReceiptStore`, `InMemoryReceiptStore` | Append-only receipt home (idempotent per `transaction_id`) + process-local impl |
| `PaymentExecutionError` | Executor infrastructure failure (chain verified, intent consumed, charge failed) |
| `DelegatedPurchaseAgent`, `DelegationRegistryFullError` | Human-not-present buyer over pre-signed intents; bounded-registry error |

All are exported from `core.world_model`.

### See also

- [Security — Agent-Initiated Commerce Replay Protection](../advanced/security.md#agent-initiated-commerce-replay-protection)
- [Audit Trail — `payment.executed` / `payment.failed`](audit-trail.md#what-gets-recorded)
- [Config — `CACHE_REDIS_URL`](config.md#storage-config)
- [Agentic Patterns](../architecture/agentic-patterns.md)
