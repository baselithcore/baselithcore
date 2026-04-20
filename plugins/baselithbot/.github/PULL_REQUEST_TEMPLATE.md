<!-- PR title must follow Conventional Commits (feat:, fix:, refactor:, docs:, test:, chore:, perf:, build:, ci:) -->

## Summary

<!-- What changes, and why? 1-3 sentences. -->

## Linked issues

Closes #

## Type of change

- [ ] `fix:` bug fix (non-breaking)
- [ ] `feat:` new capability (non-breaking)
- [ ] `feat!:` or `fix!:` — BREAKING CHANGE
- [ ] `refactor:` internal cleanup, no behavior change
- [ ] `docs:` / `test:` / `chore:` / `ci:` — non-code or tooling

## Testing

<!-- How did you verify? paste commands + outputs if useful. -->

- [ ] `pytest tests/plugins/baselithbot` green locally
- [ ] `pytest -m slow tests/plugins/baselithbot` green (if touching scheduler / sessions / replay / async lifecycles)
- [ ] `ruff check .` clean
- [ ] `mypy` via `scripts/check_official_plugin_typing.py` clean
- [ ] `scripts/check_architecture_boundaries.py` clean

## Documentation

- [ ] `CHANGELOG.md` updated under `## [Unreleased]` for user-visible changes
- [ ] `docs/` updated when behavior or configuration changed
- [ ] `SECURITY.md` updated if threat surface changed

## Security review

- [ ] Touches `approvals.py`, `secret_store.py`, `secret_redaction.py`,
      `shell_exec.py`, `computer_tools.py`, `os_control.py`,
      `desktop_*`, `policies/`, `gateway/`, or `inbound/` — requires
      second reviewer.
- [ ] No secrets / tokens / real endpoints in the diff.
- [ ] No new `subprocess` calls without `shell=False` + argv list
      + explicit `# noqa: S603` justification.
