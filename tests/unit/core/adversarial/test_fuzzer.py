from core.adversarial.fuzzer import PromptFuzzer
from core.adversarial.types import AttackCategory


def test_fuzzer_initialization():
    fuzzer = PromptFuzzer()
    assert fuzzer.patterns is not None
    assert len(fuzzer.patterns) > 0


def test_generate_injection_attacks():
    fuzzer = PromptFuzzer()
    attacks = fuzzer.generate_injection_attacks(count=5)
    assert len(attacks) == 5
    assert all(a.category == AttackCategory.PROMPT_INJECTION for a in attacks)
    assert all(a.is_injection for a in attacks)


def test_generate_jailbreak_attacks():
    fuzzer = PromptFuzzer()
    attacks = fuzzer.generate_jailbreak_attacks(count=3)
    assert len(attacks) == 3
    assert all(a.category == AttackCategory.JAILBREAK for a in attacks)


def test_generate_extraction_attacks():
    fuzzer = PromptFuzzer()
    attacks = fuzzer.generate_extraction_attacks(count=3)
    assert len(attacks) == 3
    assert all(a.category == AttackCategory.DATA_EXTRACTION for a in attacks)


def test_mutate_payload():
    fuzzer = PromptFuzzer(mutation_rate=1.0)  # Force mutation
    payload = "test payload"
    mutated = fuzzer._mutate_payload(payload)
    assert mutated != payload


def test_fuzz_input():
    fuzzer = PromptFuzzer()
    base = "original"
    fuzzed = fuzzer.fuzz_input(base)
    assert len(fuzzed) > 0
    assert base not in fuzzed  # Should be modified
