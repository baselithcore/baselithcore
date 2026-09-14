"""An accepted-risk exception must not outlive the argument that justified it.

`.trivyignore.yaml` gives every CVE entry an `expired_at` so the finding
resurfaces and gets re-argued. Trivy honours that date; pip-audit takes ids on
the command line and knows nothing about it, so the flags are generated from
the register instead of written by hand — an expired entry simply stops being
emitted and the audit reports the CVE again.
"""

from datetime import date

import pytest

from scripts.audit_ignores import ignored_ids

REGISTER = """
vulnerabilities:
  - id: CVE-2026-1111
    statement: still argued
    expired_at: 2026-12-31
  - id: CVE-2026-2222
    statement: argument has lapsed
    expired_at: 2026-01-01
secrets:
  - id: github-pat
    paths: [somewhere.yaml]
"""

TODAY = date(2026, 6, 1)


@pytest.fixture
def register(tmp_path):
    path = tmp_path / ".trivyignore.yaml"
    path.write_text(REGISTER)
    return path


class TestIgnoredIds:
    def test_in_date_entries_are_emitted(self, register):
        assert ignored_ids(register, today=TODAY) == ["CVE-2026-1111"]

    def test_expired_entries_are_dropped(self, register, capsys):
        """Dropped, not silently kept: the audit must fail on it again."""
        ids = ignored_ids(register, today=TODAY)

        assert "CVE-2026-2222" not in ids
        assert "CVE-2026-2222" in capsys.readouterr().err

    def test_the_expiry_boundary_is_inclusive(self, register):
        """A entry expiring today is still in date; tomorrow it is not."""
        assert "CVE-2026-1111" in ignored_ids(register, today=date(2026, 12, 31))
        assert ignored_ids(register, today=date(2027, 1, 1)) == []

    def test_secret_exceptions_are_not_advisory_ids(self, register):
        """The register's secret entries carry no expiry and are not CVEs."""
        assert ignored_ids(register, today=TODAY) == ["CVE-2026-1111"]

    def test_a_missing_register_ignores_nothing(self, tmp_path):
        assert ignored_ids(tmp_path / "absent.yaml", today=TODAY) == []

    def test_an_entry_without_an_expiry_is_kept(self, tmp_path):
        path = tmp_path / ".trivyignore.yaml"
        path.write_text("vulnerabilities:\n  - id: CVE-2026-3333\n")

        assert ignored_ids(path, today=TODAY) == ["CVE-2026-3333"]

    def test_malformed_ids_are_refused(self, tmp_path, capsys):
        """The ids are interpolated into a shell command line."""
        path = tmp_path / ".trivyignore.yaml"
        path.write_text(
            "vulnerabilities:\n  - id: '; rm -rf /'\n  - id: CVE-2026-4444\n"
        )

        assert ignored_ids(path, today=TODAY) == ["CVE-2026-4444"]
        assert "malformed" in capsys.readouterr().err


class TestAgainstTheRealRegister:
    def test_every_live_entry_is_a_plausible_advisory_id(self):
        for advisory in ignored_ids(today=TODAY):
            assert advisory.startswith(("CVE-", "GHSA-", "PYSEC-")), advisory
