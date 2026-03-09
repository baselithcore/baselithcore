import re
import subprocess
import sys
from pathlib import Path


def run_tests():
    """Runs pytest with coverage and returns the output."""
    print("🚀 Running tests with coverage...")
    try:
        # Run pytest with coverage reporting
        result = subprocess.run(
            ["pytest", "--cov=core", "--cov-report=term", "-q"],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout + result.stderr
    except Exception as e:
        print(f"❌ Error running tests: {e}")
        sys.exit(1)


def parse_results(output):
    """Parses pytest output to extract passed/total and coverage %."""
    # Extract passed/total from summary line like "1428 passed, 15 deselected in 12.34s"
    # Or "1428 passed in 12.34s"
    passed_match = re.search(r"(\d+) passed", output)
    failed_match = re.search(r"(\d+) failed", output)
    xpassed_match = re.search(r"(\d+) xpassed", output)
    xfailed_match = re.search(r"(\d+) xfailed", output)
    skipped_match = re.search(r"(\d+) skipped", output)

    passed = int(passed_match.group(1)) if passed_match else 0
    failed = int(failed_match.group(1)) if failed_match else 0
    xpassed = int(xpassed_match.group(1)) if xpassed_match else 0
    xfailed = int(xfailed_match.group(1)) if xfailed_match else 0
    skipped = int(skipped_match.group(1)) if skipped_match else 0

    total = passed + failed + xpassed + xfailed + skipped

    # Extract coverage % from TOTAL line like "TOTAL                                            5455   2345    57%"
    coverage_match = re.search(r"TOTAL\s+\d+\s+\d+\s+(\d+)%", output)
    coverage = coverage_match.group(1) if coverage_match else "0"

    return passed, total, coverage


def update_readme(passed, total, coverage):
    """Updates README.md with the new test badge."""
    readme_path = Path("README.md")
    if not readme_path.exists():
        print("❌ README.md not found!")
        return

    content = readme_path.read_text()

    # Badge format: [![Tests: {passed} passed](https://img.shields.io/badge/Tests-1428_passed-brightgreen.svg?style=for-the-badge)](tests/)
    # New format: [![Tests: {passed}/{total} | {coverage}%](https://img.shields.io/badge/Tests-{passed}/{total}_|__{coverage}%25-brightgreen.svg?style=for-the-badge)](tests/)

    pattern = r"\[\!\[Tests: .*?\]\(https://img\.shields\.io/badge/Tests-.*?\.svg\?style=for-the-badge\)\]\(tests/\)"

    # URL encoded version for the badge link
    # "/" -> %2F, "|" -> %7C, "%" -> %25
    # IMPORTANT: Replace % first to avoid double encoding of % in subsequent replacements!
    badge_label = f"{passed}/{total} | {coverage}%"
    badge_label_encoded = (
        badge_label.replace("%", "%25")
        .replace("/", "%2F")
        .replace("|", "%7C")
        .replace(" ", "")
    )

    new_badge = f"[![Tests: {badge_label}](https://img.shields.io/badge/Tests-{badge_label_encoded}-brightgreen.svg?style=for-the-badge)](tests/)"

    new_content = re.sub(pattern, new_badge, content)

    if new_content != content:
        readme_path.write_text(new_content)
        print(
            f"✅ README.md updated: {passed}/{total} tests passed, {coverage}% coverage."
        )
    else:
        print(
            "⚠️ No changes made to README.md (badge pattern not found or already up to date)."
        )


if __name__ == "__main__":
    output = run_tests()
    passed, total, coverage = parse_results(output)
    if total == 0 and "passed" not in output:
        # Fallback if parsing failed or no tests run
        print("⚠️ Could not parse test results. Check output.")
        print(output[-500:])  # Show last 500 chars
    else:
        update_readme(passed, total, coverage)
