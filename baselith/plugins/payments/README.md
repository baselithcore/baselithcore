# Payments

Reference PSP (payment service provider) adapter for the AP2 payment-execution
seam. It ships a deterministic **mock** PSP and doubles as the template every
real adapter (Stripe, Adyen, PayPal, …) should follow. It never moves money.

## Where the pieces live

The protocol and the orchestration are framework code in
`baselith.core.world_model`: `PaymentExecutor` (the adapter protocol),
`execute_payment` (verification, receipts, delegated mode) and `CartMandate`.
PSP adapters are integrations, so they live in a plugin and not in the core.
This plugin provides one:

- `MockPSPAdapter` — in-memory PSP with configurable, deterministic outcomes.
  It captures a cart whose total is at or under the threshold and returns a
  `declined` receipt above it; transaction ids are `mockpsp_<uuid>`.

## Configuration

| Key | Effect |
| --- | --- |
| `decline_over_cents` | Cart totals strictly above this value (integer cents) are declined. Omit it to capture every charge. |

Set it next to `enabled` in the plugin's block of `configs/plugins.yaml`:

```yaml
payments:
  enabled: true
  decline_over_cents: 50000
```

## Writing a real adapter

Implement one async method:

```python
async def execute(self, cart: CartMandate, *, payment_method_ref: str) -> PaymentReceipt: ...
```

- The `cart` arrives **already verified**; `payment_method_ref` is an opaque
  token the PSP resolves to an instrument. It is never a card number and never
  a key.
- Hold API credentials as `pydantic.SecretStr`.
- Return a `declined` receipt for a refused charge. Raise only for
  infrastructure failures: `execute_payment` turns them into an `error` receipt
  plus `PaymentExecutionError`.

## Layout

| Path | Purpose |
| --- | --- |
| `manifest.yaml` | Identity, version and dependencies |
| `plugin.py` | `PaymentsPlugin`: builds the executor from the configuration |
| `adapters/mock_psp.py` | `MockPSPAdapter`; old path `payments.mock_psp` still imports |
