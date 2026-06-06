---
title: Guardrails
description: Input/output protection for security and quality
---

The `core/guardrails` module protects the system by validating input and filtering output, preventing attacks and ensuring safe responses.

## Why Guardrails Are Critical

Language models (LLMs) are powerful but vulnerable to various types of attacks and errors:

**Prompt Injection**: Malicious users can manipulate prompts to make the model do unintended things (e.g., ignore system instructions, leak data)

**Jailbreak**: Techniques to bypass model restrictions (e.g., "Pretend you're in developer mode")

**Data Leakage**: The model might expose sensitive data seen during training or in context

**PII Exposure**: Output might contain personal data that shouldn't be returned

Guardrails act as a **bidirectional firewall**:

- **Input Guard**: Validates raw user input *before* it reaches the LLM
- **Output Guard**: Filters/redacts model output *before* it reaches the user

!!! warning "Layered Security"
    Guardrails are an essential defense but don't replace other security measures like rate limiting, authentication, and audit logging.

---

## Structure

```text
core/guardrails/
├── __init__.py
├── config.py           # GuardrailsConfig (plain dataclass) + regex pattern tables
├── input_guard.py      # InputGuard, InputValidationResult (direct user input)
├── output_guard.py     # OutputGuard, OutputFilterResult
└── indirect.py         # IndirectInjectionScanner, scan_external_content
```

Public exports:

```python
from core.guardrails import (
    InputGuard, InputValidationResult,
    OutputGuard, OutputFilterResult,
    GuardrailsConfig,
    IndirectInjectionScanner, IndirectScanResult,
    IndirectFinding, IndirectFindingKind,
    scan_external_content,
)
```

---

## Configuration

`GuardrailsConfig` is a plain `@dataclass` (no env-var loading). Construct it
explicitly and pass it to a guard; both guards default to `GuardrailsConfig()`
when none is given.

```python
from core.guardrails import GuardrailsConfig

config = GuardrailsConfig(
    # input validation
    input_enabled=True,
    max_input_length=10000,
    block_injection_patterns=True,
    block_code_execution=True,
    custom_block_patterns=[r"internal-token-\d+"],
    # output filtering
    output_enabled=True,
    filter_pii=True,
    filter_harmful_content=True,
    max_output_length=50000,
    # moderation
    moderation_enabled=True,
    moderation_threshold=0.7,
    allowed_url_domains=None,
)
```

!!! note "No `GUARDRAILS_*` environment variables"
    `GuardrailsConfig` is not a Pydantic settings class — there is no
    `.env` integration. Configure it in code.

---

## Input Guard

`InputGuard` evaluates raw user input against length limits and regex pattern
batteries (prompt-injection, code-execution, and any custom patterns). The
synchronous `validate(text)` returns an `InputValidationResult`.

```python
from core.guardrails import InputGuard

guard = InputGuard()  # or InputGuard(config)

result = guard.validate(user_input)

if not result.is_valid:
    print(result.blocked_reason)      # e.g. "Potentially harmful content detected"
    print(result.detected_patterns)   # e.g. ["injection:ignore\\s+..."]
    return "Invalid input"

safe_input = result.sanitized_input   # original text when valid
```

`InputValidationResult` fields:

| Field | Type | Meaning |
|-------|------|---------|
| `is_valid` | `bool` | Whether the input passed |
| `blocked_reason` | `str \| None` | Why it was blocked |
| `detected_patterns` | `list[str] \| None` | Matched pattern labels |
| `sanitized_input` | `str \| None` | Passed-through (valid) or truncated (too long) text |

### LLM-based evaluation (async)

`validate_async(text)` first runs the synchronous regex checks, then — unless
disabled — asks an LLM to classify the input as `SAFE`/`MALICIOUS`. On any LLM
error it falls back to the regex result.

```python
result = await guard.validate_async(user_input)

if not result.is_valid:
    print(f"Blocked: {result.blocked_reason}")
    # blocked_reason == "LLM guardrail detected malicious intent" when the
    # semantic layer is what caught it
```

This layer is designed to catch complex prompt injections and jailbreaks that
slip past plain string matching.

### Sanitizing instead of blocking

`sanitize(text)` returns a copy with injection/code-execution patterns replaced
by `[REDACTED]` (it does not redact PII — that is the output guard's job):

```python
clean = guard.sanitize(user_input)
```

### Input checks

| Check | Driven by |
|-------|-----------|
| Length limit | `max_input_length` |
| Prompt injection | `block_injection_patterns` |
| Code execution | `block_code_execution` |
| Custom patterns | `custom_block_patterns` |
| Semantic (LLM) | `validate_async` only |

---

## Output Guard

`OutputGuard` filters model output before it reaches the user: it truncates
over-long output, redacts PII, and replaces harmful-content matches. The single
entry point is the synchronous `filter(text)` returning an `OutputFilterResult`.

```python
from core.guardrails import OutputGuard

guard = OutputGuard()  # or OutputGuard(config)

result = guard.filter(llm_response)

print(result.filtered_output)   # PII-redacted, harmful content masked
if not result.is_safe:
    # harmful content was detected/filtered (truncation alone stays "safe")
    print(result.warnings)      # e.g. ["harmful_content:violence"]

print(result.redactions)        # e.g. {"email": 2, "phone": 1} or None
```

`OutputFilterResult` fields:

| Field | Type | Meaning |
|-------|------|---------|
| `is_safe` | `bool` | `False` if harmful content was filtered (truncation alone keeps it `True`) |
| `filtered_output` | `str` | The cleaned text (always present) |
| `redactions` | `dict[str, int] \| None` | PII type → count redacted |
| `warnings` | `list[str] \| None` | Truncation / harmful-content notes |

PII redaction covers `email`, `phone`, `ssn`, `credit_card`, and `ip_address`,
replacing each match with `[TYPE_REDACTED]`. `check_safety(text)` is a
lightweight boolean probe for harmful patterns without producing a result.

!!! note "Output guard API"
    `OutputGuard` exposes `filter(text)` and `check_safety(text)` only — there
    is no `process(...)` or `sanitize(...)` method. (`process(...)` is also not
    defined on `InputGuard`.)

---

## Complete Pipeline

```python
from core.guardrails import InputGuard, OutputGuard

input_guard = InputGuard()
output_guard = OutputGuard()

async def safe_chat(user_input: str) -> str:
    # 1. Validate input (sync regex + optional async LLM)
    input_result = await input_guard.validate_async(user_input)
    if not input_result.is_valid:
        return "Cannot process this request."

    # 2. Generate response
    response = await llm.generate(input_result.sanitized_input)

    # 3. Filter output
    output_result = output_guard.filter(response)
    return output_result.filtered_output
```

---

## Indirect Injection Scanning

`InputGuard` inspects what the **user** typed. It does not see instructions smuggled inside content the agent fetches itself — web pages, emails, documents, tool output. **Indirect prompt injection** hides agent directives in that data so they never pass through the user prompt.

`IndirectInjectionScanner` (`core/guardrails/indirect.py`) scans any blob of untrusted external content **before it enters the model's context window**. It is cheap (pure regex + unicode inspection) and detection-first: you decide whether to block, redact, or flag.

It catches:

| Finding kind     | What it detects |
| ---------------- | --------------- |
| `zero_width`     | Zero-width / invisible characters (U+200B, U+200C, U+2060, BOM, …) used to hide text |
| `bidi_override`  | Bidirectional text-direction override / isolate characters (text spoofing) |
| `html_comment`   | HTML comments whose body reads as an agent instruction |
| `hidden_css`     | CSS that visually hides text while keeping it in the source (`display:none`, `font-size:0`, white-on-white, off-screen) |
| `ai_directive`   | Agent-directed phrases ("ignore all previous instructions", "forward … to …@…", `send_email`, …) |

```python
from core.guardrails import IndirectInjectionScanner

scanner = IndirectInjectionScanner()

# Scan fetched content before passing it to the model
result = scanner.scan(fetched_html)
if result.is_suspicious:
    for finding in result.findings:
        log.warning("indirect injection", kind=finding.kind.value, detail=finding.detail)

# Or neutralize: strip invisibles + HTML comments, keep the human-visible text
clean = scanner.sanitize(fetched_html)
```

!!! tip "Where to run it"
    Run the scanner on **every** web page, email, or document the agent ingests via a tool — that is where indirect injection lives. The direct-input `InputGuard` will not catch these because it scans the user prompt, not the fetched data.

### `scan_external_content` — the ingestion-boundary helper

`scan_external_content(content, *, source, sanitize=None)` is the recommended
one-call entry point for ingestion boundaries. It scans, logs any findings with
the `source` label for triage, and returns the content:

```python
from core.guardrails import scan_external_content

text = scan_external_content(tool_output, source=f"mcp_tool:{name}")
```

- **Log-only by default** (additive): the original content is returned
  unchanged, so wiring it in cannot alter what reaches the model.
- **Opt-in sanitizing**: pass `sanitize=True`, or set
  `BASELITH_SANITIZE_EXTERNAL_CONTENT=true`, to strip invisibles, bidi
  characters, and instruction-bearing HTML comments before the content is used.

It is already wired into the framework's untrusted-content boundaries:

| Boundary | Location | `source` label |
|----------|----------|----------------|
| External MCP tool results | `core/mcp/client.py` (`MCPClient.call_tool`) | `mcp_tool:<name>` |
| Scraped pages (HTTP) | `plugins/web_scraper/fetchers/httpx_fetcher.py` | `web_scraper:<url>` |
| Scraped pages (rendered) | `plugins/web_scraper/fetchers/playwright_fetcher.py` | `web_scraper:<url>` |
