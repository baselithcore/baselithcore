"""Tests for the SDK ↔ OpenAPI contract gate (scripts/check_sdk_contract.py)."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.check_sdk_contract import (
    check_sdk_contract,
    python_client_calls,
    resolve,
    typescript_client_calls,
)

PY_SOURCE = """
class Client:
    def _url(self, path, *, versioned=True):
        return path

    def chat(self, query):
        return self._request("POST", "/chat", json={})

    def health(self):
        return self._request("GET", "/health", versioned=False)

    def chat_stream(self, query):
        url = self._url("/chat/stream")
        with self._http.stream("POST", url, json={}) as resp:
            yield from resp.iter_text()
"""

TS_SOURCE = """
export class Client {
  async chat(query: string) {
    const res = await this.request('POST', '/chat', { body });
    return res;
  }
  async health() {
    return this.request('GET', '/health', { versioned: false });
  }
  async *chatStream(query: string) {
    const res = await this.rawFetch(this.url('/chat/stream'), {
      method: 'POST',
      headers: this.headers({}),
      body: JSON.stringify(body),
    });
  }
}
"""


class TestPythonExtraction:
    def test_reads_the_verb_from_the_streaming_call(self, tmp_path: Path) -> None:
        """The streaming path assigns the URL first; the verb is on `.stream()`.

        Assuming GET here is exactly the bug this test pins: the gate would
        report a phantom mismatch against a spec that declares POST.
        """
        source = tmp_path / "client.py"
        source.write_text(PY_SOURCE, encoding="utf-8")

        assert python_client_calls(source) == {
            ("POST", "/chat"),
            ("GET", "/health"),
            ("POST", "/chat/stream"),
        }


class TestTypeScriptExtraction:
    def test_reads_the_verb_from_the_options_object(self, tmp_path: Path) -> None:
        source = tmp_path / "client.ts"
        source.write_text(TS_SOURCE, encoding="utf-8")

        assert typescript_client_calls(source) == {
            ("POST", "/chat"),
            ("GET", "/health"),
            ("POST", "/chat/stream"),
        }


class TestResolve:
    def test_versioned_routes_get_the_prefix(self) -> None:
        assert resolve("/chat") == "/v1/chat"

    def test_probes_stay_unversioned(self) -> None:
        assert resolve("/health") == "/health"
        assert resolve("/health/ready") == "/health/ready"


class TestShippedSdks:
    def test_the_committed_sdks_match_the_committed_spec(self) -> None:
        violations = check_sdk_contract()

        assert violations == [], "\n".join(violations)

    def test_both_clients_are_actually_parsed(self) -> None:
        """A regex that stops matching would make the gate pass vacuously."""
        assert python_client_calls(), "no routes parsed from the Python client"
        assert typescript_client_calls(), "no routes parsed from the TypeScript client"

    def test_every_sdk_route_exists_in_the_spec(self) -> None:
        spec_path = Path(__file__).resolve().parents[2] / "sdk" / "openapi.json"
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        for method, route in python_client_calls():
            operations = spec["paths"].get(resolve(route), {})
            assert method.lower() in operations, f"{method} {resolve(route)}"
