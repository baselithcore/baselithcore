"""Deterministic code security review — regex/entropy scanner, no LLM.

Pins the contract of :func:`core.guardrails.code_review.review_code`:
every secret class is detected with a line number and ``high`` severity,
dangerous call patterns are flagged ``medium``/``high``, the entropy check
catches only genuinely random-looking literals, and clean code is approved
with severity ``"none"``.
"""

from __future__ import annotations

from core.guardrails.code_review import (
    CodeReview,
    CodeReviewComment,
    review_code,
)


def _flagged_lines(review: CodeReview) -> list[int | None]:
    return [comment.line for comment in review.comments]


# ---------------------------------------------------------------------------
# Secret detection (severity high)
# ---------------------------------------------------------------------------


def test_aws_access_key_flagged_high_with_line_number() -> None:
    code = 'import boto3\n\nkey = "AKIAIOSFODNN7EXAMPLE"\n'
    review = review_code(code)

    assert review.verdict == "flagged"
    assert review.severity == "high"
    assert any(
        c.severity == "high" and "aws" in c.message.lower() and c.line == 3
        for c in review.comments
    )


def test_github_classic_token_flagged() -> None:
    token = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
    review = review_code(f'GITHUB = "{token}"')

    assert review.verdict == "flagged"
    assert review.severity == "high"
    assert any("github" in c.message.lower() and c.line == 1 for c in review.comments)


def test_github_fine_grained_token_flagged() -> None:
    token = "github_pat_11ABCDEFG0_abcdefghij1234567890abcdefghij"
    review = review_code(f"header = 'Bearer {token}'")

    assert review.verdict == "flagged"
    assert any("github" in c.message.lower() for c in review.comments)


def test_openai_style_key_flagged() -> None:
    review = review_code('client = OpenAI(api_key="sk-Abc123Def456Ghi789Jkl012")')

    assert review.verdict == "flagged"
    assert review.severity == "high"
    assert any("key" in c.message.lower() and c.line == 1 for c in review.comments)


def test_slack_token_flagged() -> None:
    # Split like the GitHub fixtures above: a contiguous token literal in the
    # source trips upstream push protection on the very file whose job is
    # detecting them. Reassembled at runtime, so the scanner still sees one.
    token = "xoxb" + "-123456789012-abcdefghijklmnop"
    code = f'SLACK = "{token}"'
    review = review_code(code)

    assert review.verdict == "flagged"
    assert any("slack" in c.message.lower() and c.line == 1 for c in review.comments)


def test_private_key_block_flagged() -> None:
    code = "data = '''\n-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA\n'''"
    review = review_code(code)

    assert review.verdict == "flagged"
    assert review.severity == "high"
    assert any(
        "private key" in c.message.lower() and c.line == 2 for c in review.comments
    )


def test_high_entropy_literal_assigned_to_secret_identifier_flagged() -> None:
    code = 'API_KEY = "Zq7!mR2@pX9#kL4$wN8^vB5&"'
    review = review_code(code)

    assert review.verdict == "flagged"
    assert review.severity == "high"
    assert any("entropy" in c.message.lower() and c.line == 1 for c in review.comments)


def test_low_entropy_assignment_passes() -> None:
    review = review_code('password = "aaaaaaaaaaaaaaaaaaaaaaaa"')

    assert review.verdict == "approved"
    assert review.severity == "none"
    assert review.comments == []


def test_short_literal_assigned_to_secret_identifier_passes() -> None:
    review = review_code('token = "abc123"')

    assert review.verdict == "approved"


# ---------------------------------------------------------------------------
# Dangerous patterns
# ---------------------------------------------------------------------------


def test_eval_on_non_literal_flagged_high() -> None:
    review = review_code("result = eval(user_input)")

    assert review.verdict == "flagged"
    assert any(
        c.severity == "high" and "eval" in c.message and c.line == 1
        for c in review.comments
    )


def test_eval_on_string_literal_not_flagged() -> None:
    review = review_code('result = eval("1 + 1")')

    assert review.verdict == "approved"


def test_exec_on_non_literal_flagged_high() -> None:
    review = review_code("exec(compiled_payload)")

    assert review.verdict == "flagged"
    assert any(c.severity == "high" and "exec" in c.message for c in review.comments)


def test_method_named_eval_not_flagged() -> None:
    review = review_code("model.eval()")

    assert review.verdict == "approved"


def test_subprocess_shell_true_flagged_medium() -> None:
    review = review_code("subprocess.run(cmd, shell=True)")

    assert review.verdict == "flagged"
    assert review.severity == "medium"
    assert any("shell=True" in c.message and c.line == 1 for c in review.comments)


def test_pickle_loads_flagged_medium() -> None:
    review = review_code("obj = pickle.loads(blob)")

    assert review.verdict == "flagged"
    assert any(
        c.severity == "medium" and "pickle" in c.message.lower()
        for c in review.comments
    )


def test_yaml_load_without_loader_flagged() -> None:
    review = review_code("cfg = yaml.load(stream)")

    assert review.verdict == "flagged"
    assert any("yaml" in c.message.lower() and c.line == 1 for c in review.comments)


def test_yaml_load_with_loader_kwarg_not_flagged() -> None:
    review = review_code("cfg = yaml.load(stream, Loader=yaml.SafeLoader)")

    assert review.verdict == "approved"


def test_yaml_safe_load_not_flagged() -> None:
    review = review_code("cfg = yaml.safe_load(stream)")

    assert review.verdict == "approved"


def test_requests_verify_false_flagged_medium() -> None:
    review = review_code("requests.get(url, verify=False)")

    assert review.verdict == "flagged"
    assert any("verify=False" in c.message for c in review.comments)


def test_os_system_flagged_medium() -> None:
    review = review_code('os.system("ls -la " + path)')

    assert review.verdict == "flagged"
    assert any(
        c.severity == "medium" and "os.system" in c.message for c in review.comments
    )


# ---------------------------------------------------------------------------
# Verdict aggregation / clean code
# ---------------------------------------------------------------------------


def test_clean_code_approved() -> None:
    code = (
        "def add(a: int, b: int) -> int:\n"
        '    """Sum two integers."""\n'
        "    return a + b\n"
    )
    review = review_code(code, filename="math_utils.py")

    assert review.verdict == "approved"
    assert review.severity == "none"
    assert review.comments == []


def test_empty_text_approved() -> None:
    assert review_code("").verdict == "approved"


def test_severity_is_max_across_comments() -> None:
    code = (
        "import requests\n"
        "resp = requests.get(url, verify=False)\n"
        'aws = "AKIAIOSFODNN7EXAMPLE"\n'
    )
    review = review_code(code)

    assert review.verdict == "flagged"
    assert review.severity == "high"
    assert sorted(_flagged_lines(review)) == [2, 3]


def test_comment_dataclass_shape() -> None:
    comment = CodeReviewComment(severity="low", message="note", line=None)

    assert comment.severity == "low"
    assert comment.line is None
