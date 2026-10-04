"""Integration-suite policy: in the CI integration job a skip is a failure.

Every real-service test here skips when its backend is unreachable, which keeps
the default unit run (no services, psycopg mocked) green. In the job that
starts those services the same skip would hide a broken service definition, a
renamed env var or a dead health check — the suite would report green while
testing nothing, which is how the old ``python_test`` job ran for months with
a Postgres container nothing connected to.

``BASELITH_TEST_INTEGRATION_STRICT=1`` (set by the ``integration_test`` job in
``.github/workflows/ci.yml``) turns every skip under ``tests/integration/``
into a failure that carries the original reason.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from typing import Any

import pytest

_STRICT = os.environ.get("BASELITH_TEST_INTEGRATION_STRICT", "").strip().lower() in {
    "1",
    "true",
    "yes",
}


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[Any]
) -> Generator[None, Any, None]:
    """Report a skip as a failure when strict mode is on."""
    outcome = yield
    if not _STRICT:
        return
    report = outcome.get_result()
    if report.skipped and not hasattr(report, "wasxfail"):
        reason = report.longrepr[2] if isinstance(report.longrepr, tuple) else ""
        report.outcome = "failed"
        report.longrepr = (
            f"skipped under BASELITH_TEST_INTEGRATION_STRICT=1: {reason}\n"
            "The integration job starts every backend this suite needs; a skip "
            "here means a service or its env wiring is broken."
        )
