"""Unit tests for secret_redaction.

Secret redaction is defense-in-depth for audit logs; regression here is
high-severity because it directly determines what leaks into JSONL files
and the observability backend.
"""

from __future__ import annotations

import pytest

from plugins.baselithbot.secret_redaction import redact_payload


class TestRedactKeys:
    @pytest.mark.parametrize(
        "key",
        [
            "token",
            "api_key",
            "apikey",
            "password",
            "secret",
            "access_token",
            "bot_token",
            "webhook_url",
            "auth_token",
            "private_key",
            "session_cookie",
            "authorization",
            "bearer",
            "SLACK_BOT_TOKEN",  # key-pattern match is case-insensitive
        ],
    )
    def test_sensitive_key_is_redacted(self, key: str) -> None:
        result = redact_payload({key: "super-secret-value-1234567890"})
        assert result[key] == "<redacted>"

    def test_non_sensitive_key_is_untouched(self) -> None:
        result = redact_payload({"user_id": 42, "goal": "go shopping"})
        assert result == {"user_id": 42, "goal": "go shopping"}


class TestRedactStrings:
    def test_bearer_token_masked_in_free_text(self) -> None:
        result = redact_payload("Authorization: Bearer abc123.def-ghi_jkl")
        assert "Bearer <redacted>" in result
        assert "abc123" not in result

    def test_long_tokenlike_strings_truncated(self) -> None:
        token = "A" * 40
        result = redact_payload(f"leaked={token}")
        assert token not in result
        assert "<redacted:" in result

    def test_short_strings_passthrough(self) -> None:
        assert redact_payload("hello world") == "hello world"


class TestRedactNested:
    def test_nested_dict_is_masked(self) -> None:
        payload = {
            "meta": {"user": "alice", "api_key": "kkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkk"},
            "list": [{"token": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"}],
        }
        result = redact_payload(payload)
        assert result["meta"]["api_key"] == "<redacted>"
        assert result["list"][0]["token"] == "<redacted>"
        assert result["meta"]["user"] == "alice"

    def test_max_depth_truncates_deep_recursion(self) -> None:
        deep: dict = {"k": {}}
        cursor = deep
        for _ in range(12):
            cursor["k"] = {"k": {}}
            cursor = cursor["k"]
        result = redact_payload(deep, max_depth=3)

        def _has_truncation_marker(node: object) -> bool:
            if node == "<truncated>":
                return True
            if isinstance(node, dict):
                return any(_has_truncation_marker(v) for v in node.values())
            return False

        assert _has_truncation_marker(result)

    def test_tuple_preserves_shape(self) -> None:
        result = redact_payload(("safe", {"token": "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz"}))
        assert isinstance(result, tuple)
        assert result[1]["token"] == "<redacted>"
