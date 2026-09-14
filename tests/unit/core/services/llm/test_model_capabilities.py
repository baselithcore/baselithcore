"""Unit tests for the model-family capability table."""

import pytest

from core.services.llm.model_capabilities import (
    ThinkingMode,
    capabilities_for,
    clamp_effort,
    default_max_tokens,
    rejects_forced_tool_choice,
    supports_sampling_params,
)

ADAPTIVE_NO_SAMPLING = [
    "claude-fable-5-1",
    "claude-fable-5",
    "claude-mythos-5-1",
    "claude-mythos-5",
    "claude-opus-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-sonnet-5",
]


class TestThinkingModes:
    @pytest.mark.parametrize("model", ADAPTIVE_NO_SAMPLING)
    def test_modern_families_are_adaptive_and_reject_sampling(self, model):
        caps = capabilities_for(model)
        assert caps.thinking_mode is ThinkingMode.ADAPTIVE
        assert caps.supports_sampling_params is False
        assert supports_sampling_params(model) is False

    @pytest.mark.parametrize("model", ["claude-opus-4-6", "claude-sonnet-4-6"])
    def test_4_6_is_adaptive_but_still_accepts_sampling(self, model):
        caps = capabilities_for(model)
        assert caps.thinking_mode is ThinkingMode.ADAPTIVE
        assert caps.supports_sampling_params is True

    def test_haiku_4_5_uses_the_budget_form(self):
        caps = capabilities_for("claude-haiku-4-5")
        assert caps.thinking_mode is ThinkingMode.BUDGET
        assert caps.supports_sampling_params is True

    def test_unknown_model_falls_back_to_legacy_budget_mode(self):
        caps = capabilities_for("gpt-4o")
        assert caps.thinking_mode is ThinkingMode.BUDGET
        assert caps.supports_sampling_params is True

    def test_unknown_claude_5x_defaults_to_adaptive(self):
        caps = capabilities_for("claude-newthing-5-3")
        assert caps.thinking_mode is ThinkingMode.ADAPTIVE
        assert caps.supports_sampling_params is False

    def test_unknown_claude_4_7_defaults_to_adaptive(self):
        assert capabilities_for("claude-newthing-4-9").thinking_mode is (
            ThinkingMode.ADAPTIVE
        )

    def test_unknown_claude_4_6_stays_legacy(self):
        assert capabilities_for("claude-newthing-4-6").thinking_mode is (
            ThinkingMode.BUDGET
        )

    def test_empty_model_is_legacy(self):
        assert capabilities_for("").thinking_mode is ThinkingMode.BUDGET


class TestEffortLevels:
    @pytest.mark.parametrize("model", ADAPTIVE_NO_SAMPLING)
    def test_modern_families_accept_xhigh_and_max(self, model):
        caps = capabilities_for(model)
        assert "xhigh" in caps.supports_effort_levels
        assert "max" in caps.supports_effort_levels

    @pytest.mark.parametrize("model", ["claude-opus-4-6", "claude-sonnet-4-6"])
    def test_4_6_has_no_xhigh(self, model):
        caps = capabilities_for(model)
        assert "xhigh" not in caps.supports_effort_levels
        assert "high" in caps.supports_effort_levels

    def test_clamp_keeps_a_supported_tier(self):
        assert clamp_effort("claude-opus-5", "xhigh") == "xhigh"
        assert clamp_effort("claude-opus-5", "max") == "max"

    def test_clamp_lowers_unsupported_tiers_to_high(self):
        assert clamp_effort("claude-opus-4-6", "xhigh") == "high"
        assert clamp_effort("claude-sonnet-4-6", "max") == "high"

    def test_clamp_passes_through_base_tiers(self):
        assert clamp_effort("claude-opus-4-6", "medium") == "medium"

    def test_clamp_unknown_tier_returns_none(self):
        assert clamp_effort("claude-opus-5", "turbo") is None
        assert clamp_effort("claude-opus-5", None) is None


class TestForcedToolChoice:
    def test_5_1_families_reject_forced_tool_choice(self):
        assert rejects_forced_tool_choice("claude-fable-5-1") is True
        assert rejects_forced_tool_choice("claude-mythos-5-1") is True

    def test_other_families_accept_it(self):
        assert rejects_forced_tool_choice("claude-fable-5") is False
        assert rejects_forced_tool_choice("claude-opus-5") is False
        assert rejects_forced_tool_choice("gpt-4o") is False


class TestMaxTokenDefaults:
    def test_non_streaming_default(self):
        assert default_max_tokens("claude-opus-5") == 16000

    def test_streaming_default_is_larger(self):
        assert default_max_tokens("claude-opus-5", streaming=True) == 64000


class TestPrefixMatching:
    def test_longest_prefix_wins(self):
        # ``claude-fable-5-1`` must not be served by the ``claude-fable-5`` row.
        assert rejects_forced_tool_choice("claude-fable-5-1") is True
        assert rejects_forced_tool_choice("claude-fable-5") is False

    def test_matching_is_case_insensitive_and_trims(self):
        caps = capabilities_for("  Claude-Opus-5  ")
        assert caps.thinking_mode is ThinkingMode.ADAPTIVE

    def test_bedrock_style_prefixed_id_still_matches(self):
        # Bedrock/Vertex ids carry a vendor prefix and a version suffix.
        caps = capabilities_for("us.anthropic.claude-opus-5-v1:0")
        assert caps.thinking_mode is ThinkingMode.ADAPTIVE
        assert caps.supports_sampling_params is False


class TestLegacyNamingScheme:
    """Pre-4 ids put the version first — they are not modern families."""

    @pytest.mark.parametrize(
        "model",
        [
            "claude-3-5-sonnet-20240620",
            "claude-3-7-sonnet-20250219",
            "claude-3-opus-20240229",
        ],
    )
    def test_dated_ids_stay_on_the_legacy_profile(self, model):
        caps = capabilities_for(model)
        assert caps.thinking_mode is ThinkingMode.BUDGET
        assert caps.supports_sampling_params is True


class TestUnlistedModernIds:
    """An id the table does not carry gets the safe half of the profile."""

    def test_unlisted_modern_id_keeps_base_effort_tiers_only(self):
        # Adaptive thinking and no sampling params are safe guesses for a 5.x
        # release; xhigh/max are not — a family that lacks them answers 400.
        caps = capabilities_for("claude-newthing-5-3")
        assert caps.thinking_mode is ThinkingMode.ADAPTIVE
        assert caps.supports_sampling_params is False
        assert "xhigh" not in caps.supports_effort_levels
        assert "max" not in caps.supports_effort_levels
        assert "high" in caps.supports_effort_levels

    def test_unlisted_modern_id_clamps_xhigh_down(self):
        assert clamp_effort("claude-newthing-5-3", "xhigh") == "high"
        assert clamp_effort("claude-newthing-4-9", "max") == "high"

    @pytest.mark.parametrize(
        "model",
        ["claude-opus-5", "claude-fable-5-1", "claude-mythos-5", "claude-sonnet-5"],
    )
    def test_listed_rows_keep_their_explicit_tiers(self, model):
        assert clamp_effort(model, "xhigh") == "xhigh"
        assert clamp_effort(model, "max") == "max"


class TestLegacyTokenCaps:
    """The legacy row serves models whose output cap is 8192."""

    def test_legacy_defaults_stay_inside_the_old_output_cap(self):
        assert default_max_tokens("claude-3-5-sonnet-20240620") == 4096
        assert default_max_tokens("claude-3-5-sonnet-20240620", streaming=True) == 8192

    def test_unknown_non_claude_models_use_the_fallback_floor(self):
        assert default_max_tokens("gpt-4o") == 4096
        assert default_max_tokens("gpt-4o", streaming=True) == 4096

    def test_modern_rows_keep_the_large_caps(self):
        assert default_max_tokens("claude-opus-5") == 16000
        assert default_max_tokens("claude-opus-5", streaming=True) == 64000


class TestHaiku45Row:
    """Haiku 4.5 is current: big output cap, budget thinking, no effort."""

    def test_output_caps_match_the_current_generation(self):
        assert default_max_tokens("claude-haiku-4-5") == 16000
        assert default_max_tokens("claude-haiku-4-5", streaming=True) == 64000

    def test_thinking_is_the_budget_form_with_sampling_allowed(self):
        caps = capabilities_for("claude-haiku-4-5")
        assert caps.thinking_mode is ThinkingMode.BUDGET
        assert caps.supports_sampling_params is True

    def test_no_effort_tier_is_supported(self):
        # ``effort`` errors on this family, so there is no tier to fall back
        # to: it is dropped, not clamped.
        assert (
            capabilities_for("claude-haiku-4-5").supports_effort_levels == frozenset()
        )
        assert clamp_effort("claude-haiku-4-5", "high") is None
        assert clamp_effort("claude-haiku-4-5", "max") is None

    def test_genuinely_old_ids_keep_the_small_caps(self):
        assert default_max_tokens("claude-3-5-sonnet-20240620") == 4096
        assert default_max_tokens("claude-3-5-sonnet-20240620", streaming=True) == 8192


class TestPre35OutputCeilings:
    """The oldest families cap at 4096; only some legacy ids reach 8192."""

    @pytest.mark.parametrize(
        "model",
        [
            "claude-3-opus-20240229",
            "claude-3-haiku-20240307",
            "claude-3-sonnet-20240229",
        ],
    )
    def test_a_4096_model_never_gets_an_8192_stream_default(self, model):
        # Streaming with no explicit max_tokens used to send 8192 and 400.
        assert default_max_tokens(model, streaming=True) == 4096
        assert default_max_tokens(model) == 4096

    @pytest.mark.parametrize(
        "model",
        [
            "claude-3-5-sonnet-20240620",
            "claude-3-5-sonnet-latest",
            "claude-3-5-haiku-20241022",
        ],
    )
    def test_the_8192_capable_ids_have_a_row_of_their_own(self, model):
        assert default_max_tokens(model, streaming=True) == 8192
        assert default_max_tokens(model) == 4096

    def test_a_row_is_what_makes_their_ceiling_enforceable(self):
        from core.services.llm.model_capabilities import clamp_max_tokens

        assert clamp_max_tokens("claude-3-5-sonnet-20240620", 20000) == 8192

    @pytest.mark.parametrize(
        "model", ["claude-2.1", "claude-instant-1.2", "some-unknown-model"]
    )
    def test_the_fallback_is_the_floor_every_family_supports(self, model):
        # Unlisted means unknown: the default has to be the value that cannot
        # be refused, not the largest one some of them happen to accept.
        assert default_max_tokens(model, streaming=True) == 4096
        assert default_max_tokens(model) == 4096

    def test_the_legacy_ids_keep_budget_thinking_and_sampling(self):
        for model in ("claude-3-5-sonnet-20240620", "claude-3-opus-20240229"):
            caps = capabilities_for(model)
            assert caps.thinking_mode is ThinkingMode.BUDGET
            assert caps.supports_sampling_params is True
            assert caps.supports_effort_levels == frozenset()
