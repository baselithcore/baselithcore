"""The plugin-integrity collector (CC8.1): deployed plugins against their signatures."""

from __future__ import annotations

from baselith.core.compliance.soc2 import EvidenceOutcome


def _plugin(tmp_path, name: str, *, declared: str | None, body: str = "x = 1\n"):
    directory = tmp_path / name
    directory.mkdir()
    (directory / "plugin.py").write_text(body)
    manifest = {"name": name}
    if declared is not None:
        manifest["integrity_sha256"] = declared
    import yaml

    (directory / "manifest.yaml").write_text(yaml.safe_dump(manifest))
    return directory


def _signed(tmp_path, name: str):
    from baselith.core.plugins.integrity import compute_plugin_hash

    directory = _plugin(tmp_path, name, declared=None)
    digest = compute_plugin_hash(directory)
    import yaml

    (directory / "manifest.yaml").write_text(
        yaml.safe_dump({"name": name, "integrity_sha256": digest})
    )
    return directory


async def _run_integrity(monkeypatch, entries):
    from baselith.core.compliance.soc2 import collectors as module

    class _Plugin:
        def __init__(self, name):
            self.metadata = type("M", (), {"name": name})()

    class _Registry:
        def get_all(self):
            return [_Plugin(n) for n, _ in entries]

        def get_plugin_directory(self, name):
            return dict(entries)[name]

    monkeypatch.setattr(
        "baselith.core.di.container.ServiceRegistry.get",
        classmethod(lambda cls, _key: _Registry()),
    )
    return await module._plugin_integrity()


async def test_plugins_that_match_their_signature_are_operated(tmp_path, monkeypatch):
    entries = [("a", _signed(tmp_path, "a")), ("b", _signed(tmp_path, "b"))]

    draft = await _run_integrity(monkeypatch, entries)

    assert draft.outcome is EvidenceOutcome.OPERATED
    assert draft.details["verified"] == 2 and draft.details["drifted"] == []


async def test_a_plugin_changed_after_signing_is_an_exception(tmp_path, monkeypatch):
    changed = _signed(tmp_path, "changed")
    (changed / "plugin.py").write_text("x = 2  # edited after signing\n")
    entries = [("changed", changed), ("fine", _signed(tmp_path, "fine"))]

    draft = await _run_integrity(monkeypatch, entries)

    assert draft.outcome is EvidenceOutcome.EXCEPTION
    assert draft.details["drifted"] == ["changed"]
    assert "changed" in draft.summary


async def test_an_unsigned_plugin_is_information_unless_signing_is_required(
    tmp_path, monkeypatch
):
    entries = [("loose", _plugin(tmp_path, "loose", declared=None))]
    monkeypatch.delenv("BASELITH_REQUIRE_SIGNED_PLUGINS", raising=False)

    lax = await _run_integrity(monkeypatch, entries)
    assert lax.outcome is EvidenceOutcome.INFORMATIONAL
    assert lax.details["unsigned"] == ["loose"]

    monkeypatch.setenv("BASELITH_REQUIRE_SIGNED_PLUGINS", "true")
    strict = await _run_integrity(monkeypatch, entries)
    assert strict.outcome is EvidenceOutcome.EXCEPTION
