# Payments (mock PSP)

Reference PSP adapter (mock) for the AP2 payment-execution seam

## Where things are

| Path | Purpose |
| --- | --- |
| `manifest.yaml` | Identity, version, dependencies and the integrity signature |
| `plugin.py` | The plugin class the loader instantiates |

## Running it

The plugin loads with the BaselithCore backend when enabled in `configs/plugins.yaml` (registry name `payments`).

## Conventions

This plugin follows the BaselithCore plugin rules in the root [`CLAUDE.md`](../../../CLAUDE.md): a `version` bump in `manifest.yaml` with every change, files under 500 lines, `en`/`it` parity, docs updated in the same change, plugin-scoped configuration and runtime state outside the tree.
