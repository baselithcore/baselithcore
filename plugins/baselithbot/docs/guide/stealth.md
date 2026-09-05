# Stealth

Browser stealth countermeasures applied to every Playwright context the
agent opens.

## What the page does

- **Hero + stats** — whether stealth is enabled and how many countermeasure
  layers are currently active.
- **Countermeasure matrix** — toggle `rotate_user_agent`, `mask_webdriver`
  (`navigator.webdriver = undefined`), `spoof_languages`, and
  `spoof_timezone` independently.
- **Identity profile** — edit the spoofed language list
  (`navigator.languages` + `Accept-Language`) and the spoofed IANA
  timezone (`Intl.DateTimeFormat().resolvedOptions().timeZone`).
- **User-agent pool** — the rotation pool (three built-in Chrome variants by
  default); edit to widen it.
- **Applied layers** and **coverage review** — a preview of the effective
  browser context (headers, `navigator` overrides) the current draft would
  produce, so you can sanity-check before saving.

## Backend

`GET /dash/stealth` (effective config = boot + runtime overlay),
`PUT /dash/stealth` (🔒) validates, persists to the same
`runtime_config.json` overlay used by [Computer Use](computer-use.md),
invalidates the cached agent, and emits `stealth.updated` on the SSE bus.

## Notes

Beyond the toggled fields, `stealth.py` also perturbs `navigator.plugins`,
`navigator.hardwareConcurrency`, the WebGL `UNMASKED_VENDOR_WEBGL` string,
and adds 2D canvas `ImageData` noise — these are always-on when stealth is
enabled and are not independently toggleable from the UI.
