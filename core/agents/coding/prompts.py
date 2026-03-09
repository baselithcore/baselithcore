"""
Prompt templates for the Coding Agent.
"""

SYSTEM_PROMPT = """You are an expert programmer and debugger.

When fixing code:
1. Analyze the error message carefully
2. Identify the root cause
3. Provide a minimal fix
4. Explain what was wrong

When generating code:
1. Follow best practices
2. Add type hints (for Python)
3. Include docstrings
4. Handle edge cases

When generating tests:
1. Test happy path
2. Test edge cases
3. Test error conditions
4. Use appropriate assertions

Always respond with valid code only, no markdown formatting."""


def get_fix_prompt(language: str, code: str, error: str, context: str = "") -> str:
    """Generate a prompt for fixing buggy code."""
    return f"""Fix this {language} code that has an error.

Code:
```{language}
{code}
```

Error:
{error}

{f"Context: {context}" if context else ""}

Provide only the fixed code, no explanations."""


def get_generate_prompt(
    language: str, description: str, examples: list[str] | None = None
) -> str:
    """Generate a prompt for code generation."""
    examples_text = ""
    if examples:
        examples_text = "\n\nExamples:\n" + "\n".join(f"- {ex}" for ex in examples)

    return f"""Generate {language} code for the following:

{description}
{examples_text}

Requirements:
- Include type hints
- Add docstring
- Handle edge cases
- Make it production-ready

Provide only the code, no explanations."""


def get_test_prompt(language: str, code: str, test_framework: str = "pytest") -> str:
    """Generate a prompt for test generation."""
    return f"""Generate comprehensive {test_framework} tests for this {language} code:

```{language}
{code}
```

Requirements:
- Test all public functions/methods
- Include edge cases
- Test error conditions
- Use descriptive test names
- Add docstrings to test functions

Provide only the test code, no explanations."""


def get_explain_prompt(language: str, code: str) -> str:
    """Generate a prompt for code explanation."""
    return f"""Explain this {language} code in detail:

```{language}
{code}
```

Provide:
1. Overview of what it does
2. Step-by-step explanation
3. Any potential issues or improvements"""


def get_refactor_prompt(language: str, code: str, goals: str = "") -> str:
    """Generate a prompt for code refactoring."""
    default_goals = "improve readability, follow best practices, add type hints"
    refactor_goals = goals or default_goals

    return f"""Refactor this {language} code with these goals: {refactor_goals}

```{language}
{code}
```

Provide only the refactored code, no explanations."""
