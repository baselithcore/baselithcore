"""The node reference the workflow drafter shows the model.

Kept next to, but apart from, :mod:`baselith.core.workflows.generator` for the
module size cap. A test asserts every :class:`NodeType` appears here, so adding
a node type without teaching the drafter about it fails the build.
"""

NODE_REFERENCE = """\
start      Entry point. No config. Output is the run input.
end        Exit point. No config. Output is the last node's output.
llm        config: prompt (template, required), system, model, temperature,
           max_tokens, json (true to get parsed JSON), schema (JSON Schema; the
           answer is validated and returned parsed).
classifier config: categories (>= 2 labels, each a string or {label, description,
           keywords}), text (template, default {{ output }}), default (a label),
           use_llm. Every outgoing edge needs condition_label = a label.
retrieve   config: query (template, default {{ output }}), top_k (1-50),
           collection, min_score. Output: list of {id, content, score, metadata}.
http       config: url (template), method (GET/POST/PUT/PATCH/DELETE), headers,
           query, json, body, credential (a NAME), timeout, allow_error_status.
           Output: {status, ok, content_type, body}.
template   config: template (string with {{ }} placeholders). Output: the text.
assign     config: assign {name: value-or-template}, coalesce {into, paths}.
           Sets variables usable as {{ name }}; passes the output through.
iteration  config: workflow (a nested definition), items (template resolving to
           a list), concurrency (1-16), max_items, on_error (fail|continue).
           Runs the nested workflow once per item; output is the list of results.
list       config: items (template), ops (list of one-key steps): filter
           "<expression on item>", sort {key, descending}, reverse, unique
           (key), offset, limit, pluck (key), flatten.
code       config: code (python; reads JSON from env WORKFLOW_INPUT, prints the
           result), inputs {name: template}, timeout. Needs the operator to
           enable code nodes.
condition  condition_expression on the node (e.g. "score > 0.8"). Outgoing edges
           carry condition_label "true" or "false".
parallel   Fan out to every outgoing edge. Branches must meet at one merge node.
merge      Fan in. Output: list of the branches' outputs.
human      Pauses until a person approves or denies. config: category.
           Use before any step with side effects.
transform  Pass-through unless a callable is configured; avoid it in YAML.
tool       tool_id (a registered tool), config.arguments {name: template}.
agent      agent_id (a registered agent), config.prompt (template with {input}).
subgraph   config.workflow (a nested definition); its output becomes this node's.
loop       Not supported. Model repetition with a condition edge back to an
           earlier node, or with iteration.
"""
