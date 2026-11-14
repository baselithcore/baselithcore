"""``QDRANT_PREFER_GRPC`` must reach the Qdrant client the factory builds."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from baselith.core.config.vectorstore import VectorStoreConfig
from baselith.core.services.vectorstore._provider_factory import build_provider


def _config(**overrides: object) -> SimpleNamespace:
    base = {
        "provider": "qdrant",
        "host": "qdrant",
        "port": 6333,
        "grpc_port": 6334,
        "qdrant_mode": "server",
        "qdrant_path": None,
        "qdrant_api_key": None,
        "qdrant_https": False,
        "qdrant_prefer_grpc": False,
        "request_timeout_seconds": 5.0,
    }
    return SimpleNamespace(**{**base, **overrides})


class TestPreferGrpc:
    def test_the_setting_defaults_to_rest(self) -> None:
        assert VectorStoreConfig.model_fields["qdrant_prefer_grpc"].default is False

    def test_the_env_name_binds_the_field(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("QDRANT_PREFER_GRPC", "true")

        assert VectorStoreConfig().qdrant_prefer_grpc is True

    @pytest.mark.parametrize("flag", [True, False])
    def test_the_factory_passes_it_to_the_provider(self, flag: bool) -> None:
        pytest.importorskip("qdrant_client")
        target = "baselith.core.services.vectorstore.providers.qdrant_provider.QdrantProvider"
        with patch(target) as provider:
            build_provider(_config(qdrant_prefer_grpc=flag))

        kwargs = provider.call_args.kwargs
        assert kwargs["prefer_grpc"] is flag
        assert kwargs["grpc_port"] == 6334
