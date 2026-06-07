# Feature Flags

`core.feature_flags` provides runtime feature flags with percentage rollout and
a pluggable backend. Flags are **opt-in**: with nothing registered and no
`BASELITH_FLAG_*` overrides set, `is_enabled` returns the call-site default, so
framework behaviour is unchanged.

## Usage

```python
from core.feature_flags import get_feature_flags, FeatureFlag, is_enabled

flags = get_feature_flags()
flags.register(FeatureFlag(
    name="new_router",
    default=False,
    rollout_percentage=25,         # 25% gradual rollout
    description="Experimental routing path",
))

# Simple check
if is_enabled("new_router"):
    ...

# Percentage rollout keyed by a stable subject (tenant/user/session)
if is_enabled("new_router", subject=tenant_id):
    ...
```

## Evaluation order

`is_enabled(name, subject=..., default=...)` resolves in this order:

1. **Provider override** — e.g. env `BASELITH_FLAG_NEW_ROUTER=true|false`. Wins
   over everything (kill-switch / force-on).
2. **Percentage rollout** — if `rollout_percentage > 0` and a `subject` is
   given, the flag is on for a deterministic `rollout_percentage`% of subjects
   (stable hash of `name:subject`, so raising the percentage only adds subjects).
3. **Default** — the registered flag's `default`, else the call-site `default`,
   else `False`.

## Environment overrides

| Variable | Effect |
|---|---|
| `BASELITH_FLAG_<NAME>` | Force a flag on/off (`1/true/yes/on` or `0/false/no/off`). `<NAME>` is the upper-cased flag name. |
| `FEATURE_FLAGS_BACKEND` | Select the provider: `env` (default) or a registered backend. |

## External backends

Dynamic providers (LaunchDarkly, Unleash, a DB-backed table) stay out of `core`
(Sacred Core rule). Register one at startup and select it via the env var:

```python
from core.feature_flags import register_flag_provider

register_flag_provider("unleash", lambda: MyUnleashProvider(...))
# then run with FEATURE_FLAGS_BACKEND=unleash
```

A provider only needs `get_override(name) -> bool | None` (`None` defers to
rollout/default).

## Conventions

Ship new optional behaviour behind a flag whose `default` preserves the current
(pre-flag) behaviour, then roll it out gradually — this keeps every change
additive and reversible without a redeploy when backed by a dynamic provider.
