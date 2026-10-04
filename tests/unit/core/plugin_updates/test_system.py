from __future__ import annotations

import json
from typing import Any, Callable

import httpx
from pydantic import SecretStr

from core.plugin_updates.models import Advisory, CheckReport, SystemUpdate
from core.plugin_updates.sources import GitHubReleaseSource
from core.plugin_updates.system import affects, check_system


def _rel(tag: str, *, pre: bool = False) -> dict[str, Any]:
    return {
        "tag_name": tag,
        "draft": False,
        "prerelease": pre,
        "body": "n",
        "html_url": f"https://github.com/o/r/releases/tag/{tag}",
        "published_at": "2026-09-29T10:00:00Z",
        "assets": [],
    }


def _adv(ghsa: str, severity: str, rng: str, patched: str = "9.9.9") -> dict[str, Any]:
    return {
        "ghsa_id": ghsa,
        "severity": severity,
        "summary": f"summary {ghsa}",
        "html_url": f"https://github.com/o/r/security/advisories/{ghsa}",
        "vulnerabilities": [
            {
                "package": {"ecosystem": "pip", "name": "baselith-core"},
                "vulnerable_version_range": rng,
                "patched_versions": patched,
            }
        ],
    }


def _source(
    releases: Any = None,
    advisories: Any = None,
    adv_status: int = 200,
    rel_status: int = 200,
) -> GitHubReleaseSource:
    def handler(req: httpx.Request) -> httpx.Response:
        if "security-advisories" in req.url.path:
            if adv_status != 200:
                return httpx.Response(adv_status)
            return httpx.Response(200, json=advisories or [])
        if rel_status != 200:
            return httpx.Response(rel_status)
        return httpx.Response(200, json=releases or [])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GitHubReleaseSource("https://api.github.com", SecretStr("t"), client=client)


REL = [_rel("v1.14.0"), _rel("v1.15.0"), _rel("v1.16.0"), _rel("v2.0.0-rc1", pre=True)]


async def test_behind_and_latest() -> None:
    res = await check_system("1.14.0", "o/r", source=_source(REL))
    assert res.available and res.behind == 2 and not res.major
    assert res.latest is not None and res.latest.version == "1.16.0"
    assert not res.security and res.error is None and res.repo == "o/r"


async def test_up_to_date() -> None:
    res = await check_system("1.16.0", "o/r", source=_source(REL))
    assert not res.available and res.behind == 0


async def test_major_jump() -> None:
    res = await check_system(
        "1.9.0", "o/r", source=_source([_rel("v2.1.0"), _rel("v1.9.0")])
    )
    assert res.available and res.major


async def test_security_advisory_matches() -> None:
    res = await check_system(
        "1.14.0", "o/r", source=_source(REL, [_adv("GHSA-1", "high", "< 1.14.1")])
    )
    assert res.security and res.severity == "high"
    assert [a.ghsa_id for a in res.advisories] == ["GHSA-1"]


async def test_highest_severity_wins() -> None:
    advs = [_adv("GHSA-1", "high", "< 1.14.1"), _adv("GHSA-2", "critical", "<= 1.14.0")]
    res = await check_system("1.14.0", "o/r", source=_source(REL, advs))
    assert res.severity == "critical" and len(res.advisories) == 2


async def test_non_matching_advisory() -> None:
    res = await check_system(
        "1.14.0", "o/r", source=_source(REL, [_adv("GHSA-1", "high", ">= 2.0.0")])
    )
    assert not res.security and res.severity is None and res.advisories == []


async def test_advisories_forbidden_still_reports_update() -> None:
    for status in (403, 500):
        res = await check_system(
            "1.14.0", "o/r", source=_source(REL, adv_status=status)
        )
        assert res.available and res.advisories == [] and not res.security
        assert res.error is not None and "advisories unavailable" in res.error


async def test_advisories_404_is_not_an_error() -> None:
    res = await check_system("1.14.0", "o/r", source=_source(REL, adv_status=404))
    assert res.available and res.advisories == [] and not res.security
    assert res.error is None


async def test_advisories_404_keeps_carried_security() -> None:
    prev = await check_system(
        "1.14.0",
        "o/r",
        source=_source(REL, advisories=[_adv("GHSA-1", "high", "< 1.14.1")]),
    )
    assert prev.security
    res = await check_system(
        "1.14.0", "o/r", source=_source(REL, adv_status=404), previous=prev
    )
    assert res.security and res.severity == prev.severity == "high"
    assert res.advisories == prev.advisories and res.error is None


async def test_advisories_2xx_empty_clears_carried_security() -> None:
    prev = await check_system(
        "1.14.0",
        "o/r",
        source=_source(REL, advisories=[_adv("GHSA-1", "high", "< 1.14.1")]),
    )
    res = await check_system(
        "1.14.0", "o/r", source=_source(REL, advisories=[]), previous=prev
    )
    assert not res.security and res.advisories == [] and res.error is None


async def test_releases_failure_sets_error() -> None:
    res = await check_system("1.14.0", "o/r", source=_source(rel_status=500))
    assert res.error is not None and not res.available and res.latest is None
    assert "t" != res.error and "Bearer" not in res.error


def test_affects_ranges() -> None:
    def adv(rng: str) -> Advisory:
        return Advisory(
            ghsa_id="G",
            severity="low",
            summary="",
            html_url="",
            vulnerable_range=rng,
            patched_versions="",
        )

    assert affects(adv("< 1.2.3"), "1.2.2")
    assert not affects(adv("< 1.2.3"), "1.2.3")
    assert affects(adv(">= 1.0.0, < 1.0.4"), "1.0.3")
    assert not affects(adv(">= 1.0.0, < 1.0.4"), "0.9.0")
    assert affects(adv("= 1.2.3"), "1.2.3")
    assert not affects(adv("= 1.2.3"), "1.2.4")
    # Unreadable range or version: the advisory cannot be ruled out, so it
    # counts (uncertain) rather than silently vanishing from the notice.
    assert affects(adv("garbage ~~"), "1.0.0")
    assert affects(adv(""), "1.0.0")
    assert affects(adv("< 2.0.0"), "not-a-version")


def test_old_cache_json_loads_without_system() -> None:
    raw = {"checked_at": "2026-09-29T10:00:00Z", "candidates": [], "error": None}
    report = CheckReport.model_validate_json(json.dumps(raw))
    assert report.system is None
    assert isinstance(SystemUpdate(repo="o/r", installed_version="1.0.0"), SystemUpdate)


_Handler = Callable[[httpx.Request], httpx.Response]


async def test_malformed_advisory_entries_skipped_and_bad_body_errors() -> None:
    good = _adv("GHSA-1", "HIGH", "< 9.0.0")
    src = _source(
        REL, [42, {"ghsa_id": 1}, {"ghsa_id": "X", "vulnerabilities": "no"}, good]
    )
    advs = await src.security_advisories("o/r")
    assert [a.ghsa_id for a in advs] == ["GHSA-1"] and advs[0].severity == "high"
    res = await check_system("1.14.0", "o/r", source=_source(REL, {"not": "list"}))
    assert res.error is not None and "advisories unavailable" in res.error


async def _prior() -> SystemUpdate:
    return await check_system(
        "1.14.0", "o/r", source=_source(REL, [_adv("GHSA-1", "high", "< 1.14.1")])
    )


async def test_security_survives_advisories_5xx() -> None:
    prior = await _prior()
    res = await check_system(
        "1.14.0", "o/r", source=_source(REL, adv_status=503), previous=prior
    )
    assert res.security and res.severity == "high" and len(res.advisories) == 1
    assert res.available and res.error is not None


async def test_security_survives_releases_5xx() -> None:
    prior = await _prior()
    res = await check_system(
        "1.14.0",
        "o/r",
        source=_source(rel_status=500, advisories=[_adv("GHSA-1", "high", "< 1.14.1")]),
        previous=prior,
    )
    assert res.security and res.available and res.behind == 2
    assert res.latest is not None and res.latest.version == "1.16.0"
    assert res.error is not None


async def test_fetched_empty_advisories_clear_security() -> None:
    prior = await _prior()
    res = await check_system("1.14.0", "o/r", source=_source(REL, []), previous=prior)
    assert not res.security and res.severity is None and res.error is None


async def test_no_carry_over_after_upgrade() -> None:
    prior = await _prior()
    res = await check_system(
        "1.16.0", "o/r", source=_source(REL, adv_status=503), previous=prior
    )
    assert not res.security and res.advisories == [] and not res.available


def test_advisory_without_a_range_is_kept_not_dropped() -> None:
    from core.plugin_updates._advisories import parse_advisory

    entry = _adv("GHSA-9", "high", "")
    entry["vulnerabilities"][0]["vulnerable_version_range"] = None
    advs = parse_advisory(entry)
    assert [a.ghsa_id for a in advs] == ["GHSA-9"] and advs[0].vulnerable_range == ""
