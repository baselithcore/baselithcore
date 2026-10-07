"""The default Permissions-Policy lets the page's own origin use the microphone.

Why: browser voice input (push-to-talk) calls ``getUserMedia``, which fails
outright under ``microphone=()``. ``(self)`` opens it for our own pages only;
embedded third-party frames still cannot request it and every other powerful
feature stays denied.
"""

from __future__ import annotations

from pathlib import Path

from core.config.security import SecurityConfig

_NGINX = Path(__file__).resolve().parents[4] / "deploy" / "nginx" / "nginx.conf"
_DENIED = (
    "geolocation",
    "camera",
    "payment",
    "usb",
    "magnetometer",
    "gyroscope",
    "accelerometer",
)


def _directives(policy: str) -> dict[str, str]:
    return {
        k.strip(): v.strip()
        for k, _, v in (p.partition("=") for p in policy.split(","))
    }


def test_default_allows_microphone_for_self_only(monkeypatch):
    monkeypatch.delenv("PERMISSIONS_POLICY", raising=False)
    policy = SecurityConfig().permissions_policy
    assert policy is not None
    d = _directives(policy)
    assert d["microphone"] == "(self)"
    assert "*" not in policy
    for feature in _DENIED:
        assert d[feature] == "()", feature


def test_nginx_edge_policy_matches_app_default(monkeypatch):
    monkeypatch.delenv("PERMISSIONS_POLICY", raising=False)
    line = next(
        ln
        for ln in _NGINX.read_text().splitlines()
        if ln.strip().startswith("add_header Permissions-Policy")
    )
    edge = line.split('"')[1]
    assert _directives(edge) == _directives(SecurityConfig().permissions_policy or "")
