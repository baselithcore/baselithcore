"""Credential strings must survive the trip from the environment to typed fields.

Every credential reaches the process as one flat string — `key=scope|scope,...`,
`kid:secret`, `idp_role:app_role`. The parsers are lenient on shape and strict on
secrecy: a malformed entry is skipped rather than raised, so one stray comma
cannot stop a deployment, and every credential comes back wrapped in `SecretStr`.
"""

from pydantic import SecretStr

from core.config._security_parsers import (
    coerce_to_secret_set,
    parse_algorithms,
    parse_encryption_keys,
    parse_role_map,
    parse_scoped_keys,
)


class TestCoerceToSecretSet:
    def test_comma_separated_string_becomes_a_set_of_secrets(self):
        result = coerce_to_secret_set(" a , b ,, c ")

        assert {s.get_secret_value() for s in result} == {"a", "b", "c"}
        assert all(isinstance(s, SecretStr) for s in result)

    def test_iterables_are_wrapped_without_double_wrapping(self):
        result = coerce_to_secret_set([SecretStr("a"), "b", 3])

        assert {s.get_secret_value() for s in result} == {"a", "b", "3"}

    def test_empty_input_is_an_empty_set(self):
        assert coerce_to_secret_set("") == set()
        assert coerce_to_secret_set(None) == set()


class TestParseRoleMap:
    def test_pairs_map_idp_role_to_a_lowercased_app_role(self):
        assert parse_role_map("Admins:ADMIN, Devs:user") == {
            "Admins": "admin",
            "Devs": "user",
        }

    def test_entries_without_a_separator_are_skipped(self):
        """A stray token must not become a role with an empty name."""
        assert parse_role_map("Admins:admin, garbage, :orphan, half:") == {
            "Admins": "admin"
        }

    def test_dicts_pass_through_as_strings(self):
        assert parse_role_map({1: 2}) == {"1": "2"}


class TestParseAlgorithms:
    def test_comma_separated_string_becomes_a_list(self):
        assert parse_algorithms("RS256, ES256") == ["RS256", "ES256"]

    def test_lists_pass_through(self):
        assert parse_algorithms(["RS256"]) == ["RS256"]


class TestParseScopedKeys:
    def test_key_and_pipe_separated_scopes_are_parsed(self):
        parsed = parse_scoped_keys("k1=read|WRITE, k2=admin")

        assert {k.get_secret_value(): v for k, v in parsed.items()} == {
            "k1": {"read", "write"},
            "k2": {"admin"},
        }

    def test_entries_without_scopes_or_separator_are_skipped(self):
        assert parse_scoped_keys("k1=read, broken, k2=") == {SecretStr("k1"): {"read"}}

    def test_dicts_are_rewrapped_and_their_scopes_become_sets(self):
        parsed = parse_scoped_keys({"k1": ["read", "read"]})

        assert {k.get_secret_value(): v for k, v in parsed.items()} == {"k1": {"read"}}

    def test_empty_input_is_an_empty_mapping(self):
        assert parse_scoped_keys(None) == {}


class TestParseEncryptionKeys:
    def test_kid_and_secret_pairs_are_parsed(self):
        parsed = parse_encryption_keys("k1:s1, k2:s2")

        assert {k: v.get_secret_value() for k, v in parsed.items()} == {
            "k1": "s1",
            "k2": "s2",
        }

    def test_a_bare_secret_loads_under_the_default_id(self):
        """The single-key case stays simple: no id to invent."""
        parsed = parse_encryption_keys("only-secret")

        assert parsed["default"].get_secret_value() == "only-secret"

    def test_a_secret_containing_a_colon_keeps_its_tail(self):
        parsed = parse_encryption_keys("k1:aa:bb")

        assert parsed["k1"].get_secret_value() == "aa:bb"

    def test_dicts_pass_through_wrapped(self):
        parsed = parse_encryption_keys({"k1": "s1"})

        assert isinstance(parsed["k1"], SecretStr)
