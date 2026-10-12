---
name: loop_goal_hardening
version: "1"
labels: [production]
description: Pre-flight questionnaire that hardens a raw goal into loop-ready form.
variables: [goal]
---
You are hardening a goal before an autonomous loop runs it.

Interrogate the goal below and answer with your best inference (do not ask questions back):

GOAL: {{ goal }}

Return JSON with exactly these keys:
{
    "goal": "<the goal restated as a machine-checkable termination condition>",
    "scope": "<what the loop may and may not touch>",
    "verifier_description": "<the concrete check that decides done, e.g. a command and its expected result>",
    "budget": "<attempt/cost/time bounds appropriate to the task>",
    "rollback_plan": "<how to undo the work if the loop loses>"
}
