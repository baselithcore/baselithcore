"""``describe_exception`` masks credentials an upstream error echoes back."""

from baselith.core.services.llm.exceptions import describe_exception


def test_bearer_and_key_pairs_are_masked():
    exc = RuntimeError(
        "401 Unauthorized: Authorization: Bearer sk-live-abcdef123456 api_key=sk-zzz999"
    )
    text = describe_exception(exc)
    assert "sk-live-abcdef123456" not in text and "sk-zzz999" not in text
    assert "401 Unauthorized" in text


def test_plain_messages_and_empty_exceptions_are_unchanged():
    assert (
        describe_exception(RuntimeError("model not found (404)"))
        == "model not found (404)"
    )
    assert describe_exception(TimeoutError()) == "TimeoutError"


def test_a_huge_error_body_is_bounded_before_redaction():
    from baselith.core.services.llm.exceptions import (
        _MAX_DESCRIBED_CHARS,
        describe_exception,
    )

    text = describe_exception(RuntimeError("x" * (_MAX_DESCRIBED_CHARS * 50)))
    assert len(text) <= _MAX_DESCRIBED_CHARS
