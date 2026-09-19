"""Tests for marketplace credential storage."""

from __future__ import annotations

import asyncio

import pytest

from core.marketplace.auth import CredentialsManager


class TestCredentialsFileIsNotTrusted:
    """`_load_data_sync` promises a dict; these are the ways a file can lie.

    Only `CredentialsManager` writes this file and it always dumps an object,
    but a hand-edited, truncated or half-synced file does not respect that. The
    contract says "empty dict if not found", so every unreadable shape has to
    reach the same place rather than surfacing as a TypeError inside whichever
    caller happened to run first.
    """

    @pytest.mark.parametrize(
        "content", ["[]", '"a string"', "5", "null", "true"], ids=lambda c: c[:12]
    )
    def test_a_valid_json_non_object_is_ignored(self, tmp_path, content):
        manager = CredentialsManager(directory=tmp_path)
        manager.credentials_file.write_text(content)

        assert manager._load_data_sync() == {}

    def test_the_caller_that_writes_still_works_afterwards(self, tmp_path):
        """The shape that used to raise TypeError on `data["api_key"] = ...`."""
        manager = CredentialsManager(directory=tmp_path)
        manager.credentials_file.write_text("[]")

        asyncio.run(manager.save_api_key("k-123"))

        assert asyncio.run(manager.load_api_key()) == "k-123"

    def test_undecodable_bytes_are_ignored(self, tmp_path):
        manager = CredentialsManager(directory=tmp_path)
        manager.credentials_file.write_bytes(b"\xff\xfe\x00binary")

        assert manager._load_data_sync() == {}

    def test_a_real_object_still_loads(self, tmp_path):
        manager = CredentialsManager(directory=tmp_path)
        manager.credentials_file.write_text('{"api_key": "kept"}')

        assert manager._load_data_sync() == {"api_key": "kept"}
