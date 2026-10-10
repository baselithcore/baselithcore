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

Retries run on `core.loops.EngineeredLoop` (lessons from failed attempts, a
stall guard, `max_fix_attempts`). Only Python executes, and the sandbox image
must contain pytest (`agent-sandbox:latest` built from
`core/services/sandbox/Dockerfile.sandbox` does).

Benchmark: `python -m plugins.coding_agent.evals` (six real bugs, judged by
hidden tests; exits 0 only at a 100% fix rate).

The plugin preserves backward compatibility through shims under
`core/agents/coding*`. The full reference is the CodingAgent section of
`mkdocs-site/docs/core-modules/agents.md`.

## Execution boundary

Every verified task (fix, write, refactor, tests) executes model-written code
and tests in the sandbox, never on the host: network off by default, a
read-only root filesystem and `/tmp` as the only writable path. One run writes
`solution.py` and `test_solution.py` under a private `/tmp` directory and runs
pytest in-process; its report line carries a fresh random key, a report
without the key is ignored and two with it fail the run. Code that reads
`/proc`, calls `os._exit` or registers `atexit` hooks is refused before it
runs. These checks raise the bar against code forging its own green report;
they are not a proof against code written deliberately to evade them, so the
sandbox isolation and the deterministic security review
(`core.guardrails.code_review`) still apply to every result.

## Input is data

Code, errors and instructions pass `core.guardrails.scan_external_content` and
are fenced in the prompt between `<<<DATA` and `DATA>>>`; data that spells a
fence marker is defused before it is quoted. This lowers the risk of prompt
injection without removing it, which is why the tests and the security review
decide what is returned.
