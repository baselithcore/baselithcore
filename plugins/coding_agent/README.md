# Coding Agent Plugin

Official Baselith plugin for autonomous coding workflows.

Capabilities:

- code generation
- iterative bug fixing with sandbox validation
- test generation
- code explanation
- code refactoring

The plugin preserves backward compatibility through shims under `core/agents/coding*`
while the Sacred Core migration is in progress.

## Execution boundary

`generate_code` validates Python output with an in-process `compile()` — a
syntax check that executes nothing. Earlier versions ran the whole generated
program in the sandbox to "check syntax". Other languages have no in-process
parser, so their syntax check still goes through the sandbox. `fix_code` and
`refactor_code` execute candidate code in the sandbox by design.
