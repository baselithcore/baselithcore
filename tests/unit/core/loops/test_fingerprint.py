"""Failure fingerprinting: stability, discrimination, normalization."""

from core.loops.fingerprint import failure_fingerprint, failure_lines

PYTEST_FAIL = """
=========================== short test summary info ============================
FAILED tests/test_orders.py::test_total - AssertionError: assert 3 == 4
1 failed, 213 passed in 12.4s
"""

PYTEST_FAIL_RERUN = """
=========================== short test summary info ============================
FAILED tests/test_orders.py::test_total - AssertionError: assert 3 == 4
1 failed, 213 passed in 9.8s
"""

PYTEST_OTHER = """
=========================== short test summary info ============================
FAILED tests/test_payments.py::test_refund - TypeError: unsupported operand
1 failed, 213 passed in 11.1s
"""


class TestFailureFingerprint:
    def test_identical_failure_is_stable(self):
        assert failure_fingerprint(PYTEST_FAIL) == failure_fingerprint(PYTEST_FAIL)

    def test_volatile_duration_does_not_change_the_hash(self):
        # Same failure, different wall-clock: a rerun must not look like progress.
        assert failure_fingerprint(PYTEST_FAIL) == failure_fingerprint(
            PYTEST_FAIL_RERUN
        )

    def test_different_failure_changes_the_hash(self):
        assert failure_fingerprint(PYTEST_FAIL) != failure_fingerprint(PYTEST_OTHER)

    def test_line_order_does_not_matter(self):
        a = "FAILED test_a.py::x - boom\nFAILED test_b.py::y - bang"
        b = "FAILED test_b.py::y - bang\nFAILED test_a.py::x - boom"
        assert failure_fingerprint(a) == failure_fingerprint(b)

    def test_memory_addresses_are_normalized(self):
        a = "Error: <object at 0x7f9a1c2d3e40> is not callable"
        b = "Error: <object at 0x10ab99f00> is not callable"
        assert failure_fingerprint(a) == failure_fingerprint(b)

    def test_empty_evidence_has_a_sentinel(self):
        assert failure_fingerprint("") == "empty"
        assert failure_fingerprint("   \n  ") == "empty"

    def test_unrecognized_format_still_discriminates(self):
        # No failure marker anywhere: two distinct blobs must not collapse
        # to the same hash, or every unknown failure would look like a stall.
        assert failure_fingerprint("weird output A") != failure_fingerprint("weird B")


class TestFailureLines:
    def test_only_failure_lines_are_kept(self):
        lines = failure_lines(PYTEST_FAIL)
        assert any("test_orders" in line for line in lines)
        assert not any("short test summary" in line for line in lines)

    def test_lines_are_capped(self):
        evidence = "\n".join(f"FAILED test_{i}.py::t - boom" for i in range(100))
        assert len(failure_lines(evidence, max_lines=5)) == 5
