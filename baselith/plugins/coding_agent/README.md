# Coding Agent Plugin

Official Baselith plugin (`coding-agent`) that writes, fixes, tests, explains
and refactors code, and proves what it returns: the model answers with typed
JSON holding code and pytest tests, the tests run in the core sandbox, and a
task succeeds only when they ran and passed.

Tasks:

- **fix**: tests that reproduce the bug are written first and frozen; the fix is
  retried until they pass (`tests_reproduce_bug` says whether they fail on the
  original code)
- **write**: code plus tests; the first answer's tests are frozen
- **refactor**: characterization tests must pass on the original code first;
  the refactor must keep them green
- **tests**: tests are written and run once; failing tests may point at bugs
- **explain**: any language, nothing executed

Retries run on `baselith.core.loops.EngineeredLoop` (lessons from failed attempts, a
stall guard, `max_fix_attempts`). Only Python executes, and the sandbox image
must contain pytest (`agent-sandbox:latest` built from
`baselith/core/services/sandbox/Dockerfile.sandbox` does).

Repository fixes: `fix_in_workspace(workspace, problem)` fixes one file of a
repository with the files it imports as context; Python runs against the whole
workspace on a read-only sandbox mount, other cases return an unverified
proposal with the reason (`language`, `missing_dependency`, `too_large`,
`sandbox_unavailable`, `probe_failed`).

Benchmark: `python -m baselith.plugins.coding_agent.evals` (six real bugs, judged by
hidden tests; exits 0 only at a 100% fix rate). Add `--workspace` for the
repository cases.

The plugin preserves backward compatibility through shims under
`baselith/core/agents/coding*`. The full reference is the CodingAgent section of
`mkdocs-site/docs/core-modules/agents.md`.

## What "verified" means

A verified run is a sandbox run whose single keyed report adds up (every
collected test passed, failed, errored or was skipped, at least one passed)
and in which every passing test executed at least one check: an `assert`
(counted through pytest's assertion-pass hook) or a `pytest.raises` /
`pytest.warns` call. A green run that fails either condition is not verified
(`inconsistent_report`, `no_assertions`). For `fix` and `write`, a first test
file with no `assert`, `pytest.raises`, `pytest.warns` or `pytest.approx` at
all is never frozen: the model is asked again.

The limits: the model writes both the code and the tests, so "verified" shows
the code passes the model's own tests, under a best-effort tamper screen,
inside the sandbox. It is not proof that the code is correct (a weak or
trivial assertion still counts), nor a guarantee against code written
deliberately to defeat the checks. The sandbox and the deterministic security
review are the boundary.

## Execution boundary

Every verified task (fix, write, refactor, tests) executes model-written code
and tests in the sandbox, never on the host: network off by default, a
read-only root filesystem and `/tmp` as the only writable path. One run writes
`solution.py` and `test_solution.py` under a private `/tmp` directory and runs
pytest in-process; its report line carries a fresh random key, a report
without the key is ignored and two with it fail the run. Code that reads
`/proc`, calls `os._exit` or registers `atexit` hooks is refused before it
runs, as is code that could reach the interpreter, the reporter or pytest's
machinery (frames, `sys.modules`, `__import__`, `exec`, pytest plugins and
hooks, `_pytest`). The screen reads the text NFKC-normalised, the canonical
source and every string built from constants, so a name split into pieces or
spelled with escapes is still caught. The report
line is written with functions bound before any test code is imported; a
skipped test, a session pytest ended early and a process that exits non-zero
after its report are never green. Repository runs also import pytest before
the workspace is on `sys.path`, and refuse conftest and pytest config files.
These checks raise the bar against code forging its own green report;
they are not a proof against code written deliberately to evade them, so the
sandbox isolation and the deterministic security review
(`baselith.core.guardrails.code_review`) still apply to every result.

## Input is data

Code, errors, test output, lessons from earlier attempts and instructions pass
`baselith.core.guardrails.scan_external_content` and are fenced in the prompt between
`<<<DATA` and `DATA>>>`; data that spells a fence marker (any case or spacing,
lookalike brackets or letters, HTML entities, hidden characters, line breaks,
or brackets that would join the real fence) is defused before it is quoted.
Short values quoted inline (the language, repository file names) are reduced to
one line without backticks, markers or hidden characters. This lowers the risk of prompt
injection without removing it, which is why the tests and the security review
decide what is returned.
