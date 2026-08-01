"""Tests for the MinerU OCR backend."""

import os
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from plugins.document_sources import ocr_mineru


@pytest.fixture
def sample_pdf_path():
    return Path("/tmp/sample_mineru_file.pdf")


def _install_fake_mineru(do_parse, read_fn):
    """Inject fake mineru modules into sys.modules for lazy-import tests."""
    fake_common = types.ModuleType("mineru.cli.common")
    fake_common.do_parse = do_parse
    fake_common.read_fn = read_fn
    fake_cli = types.ModuleType("mineru.cli")
    fake_cli.common = fake_common
    fake_root = types.ModuleType("mineru")
    fake_root.cli = fake_cli
    return patch.dict(
        "sys.modules",
        {"mineru": fake_root, "mineru.cli": fake_cli, "mineru.cli.common": fake_common},
    )


def _do_parse_writing_markdown(markdown: str):
    """Build a fake do_parse that writes the expected markdown output file."""

    def _do_parse(**kwargs):
        out_dir = Path(kwargs["output_dir"]) / "document" / "auto"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "document.md").write_text(markdown, encoding="utf-8")
        _do_parse.captured_kwargs = kwargs

    return _do_parse


class TestPerformMineruOcr:
    """Tests for _perform_mineru_ocr."""

    def test_success_returns_markdown(self, sample_pdf_path):
        do_parse = _do_parse_writing_markdown("# Title\n\nSome text")
        read_fn = MagicMock(return_value=b"%PDF-fake")
        with _install_fake_mineru(do_parse, read_fn):
            result = ocr_mineru._perform_mineru_ocr(sample_pdf_path)

        assert result is not None
        assert "Some text" in result
        read_fn.assert_called_once_with(sample_pdf_path)

        kwargs = do_parse.captured_kwargs
        assert kwargs["pdf_file_names"] == ["document"]
        assert kwargs["pdf_bytes_list"] == [b"%PDF-fake"]
        assert kwargs["parse_method"] == "auto"
        assert kwargs["f_dump_md"] is True
        for flag in (
            "f_draw_layout_bbox",
            "f_draw_span_bbox",
            "f_dump_middle_json",
            "f_dump_model_output",
            "f_dump_orig_pdf",
            "f_dump_content_list",
        ):
            assert kwargs[flag] is False

    def test_missing_dependency_warns_and_returns_none(self, sample_pdf_path):
        with (
            patch.dict(
                "sys.modules",
                {"mineru": None, "mineru.cli": None, "mineru.cli.common": None},
            ),
            patch.object(ocr_mineru, "warn_missing_dependency") as mock_warn,
        ):
            assert ocr_mineru._perform_mineru_ocr(sample_pdf_path) is None
            mock_warn.assert_called_once()
            assert mock_warn.call_args[0][0] == "mineru"

    def test_read_fn_error_returns_none(self, sample_pdf_path):
        do_parse = MagicMock()
        read_fn = MagicMock(side_effect=Exception("unsupported input"))
        with _install_fake_mineru(do_parse, read_fn):
            assert ocr_mineru._perform_mineru_ocr(sample_pdf_path) is None
        do_parse.assert_not_called()

    def test_do_parse_error_returns_none(self, sample_pdf_path):
        do_parse = MagicMock(side_effect=RuntimeError("inference failed"))
        read_fn = MagicMock(return_value=b"%PDF-fake")
        with _install_fake_mineru(do_parse, read_fn):
            assert ocr_mineru._perform_mineru_ocr(sample_pdf_path) is None

    def test_no_markdown_produced_returns_none(self, sample_pdf_path):
        do_parse = MagicMock()  # writes nothing
        read_fn = MagicMock(return_value=b"%PDF-fake")
        with _install_fake_mineru(do_parse, read_fn):
            assert ocr_mineru._perform_mineru_ocr(sample_pdf_path) is None

    def test_empty_markdown_returns_none(self, sample_pdf_path):
        do_parse = _do_parse_writing_markdown("   \n\n  ")
        read_fn = MagicMock(return_value=b"%PDF-fake")
        with _install_fake_mineru(do_parse, read_fn):
            assert ocr_mineru._perform_mineru_ocr(sample_pdf_path) is None

    def test_config_values_forwarded(self, sample_pdf_path):
        do_parse = _do_parse_writing_markdown("text")
        read_fn = MagicMock(return_value=b"%PDF-fake")
        with (
            _install_fake_mineru(do_parse, read_fn),
            patch.object(ocr_mineru, "MINERU_BACKEND", "vlm-http-client"),
            patch.object(ocr_mineru, "MINERU_LANG", "latin"),
            patch.object(ocr_mineru, "MINERU_FORMULA_ENABLE", False),
            patch.object(ocr_mineru, "MINERU_TABLE_ENABLE", False),
            patch.object(ocr_mineru, "MINERU_SERVER_URL", "http://mineru:30000"),
        ):
            assert ocr_mineru._perform_mineru_ocr(sample_pdf_path) == "text"

        kwargs = do_parse.captured_kwargs
        assert kwargs["backend"] == "vlm-http-client"
        assert kwargs["p_lang_list"] == ["latin"]
        assert kwargs["formula_enable"] is False
        assert kwargs["table_enable"] is False
        assert kwargs["server_url"] == "http://mineru:30000"


class TestPublicApi:
    """Tests for the public run_*_ocr_mineru wrappers."""

    def test_run_pdf_ocr_mineru_delegates(self, sample_pdf_path):
        with patch.object(
            ocr_mineru, "_perform_mineru_ocr", return_value="text"
        ) as mock_perform:
            assert ocr_mineru.run_pdf_ocr_mineru(sample_pdf_path) == "text"
            mock_perform.assert_called_once_with(sample_pdf_path)

    def test_run_image_ocr_mineru_delegates(self):
        image_path = Path("/tmp/sample_mineru_file.png")
        with patch.object(
            ocr_mineru, "_perform_mineru_ocr", return_value="text"
        ) as mock_perform:
            assert ocr_mineru.run_image_ocr_mineru(image_path) == "text"
            mock_perform.assert_called_once_with(image_path)


class TestServerUrlValidation:
    """S14: ``*-http-client`` backends must not ship documents to unsafe URLs."""

    @pytest.mark.parametrize(
        "url,ok",
        [
            ("https://mineru.example.com:30000", True),
            ("http://mineru:30000", True),  # dotless docker service name
            ("http://localhost:30000", True),
            ("http://127.0.0.1:30000", True),
            ("http://10.0.0.5:30000", True),  # private range
            ("http://evil.example.com/collect", False),  # plaintext to public FQDN
            ("http://93.184.216.34/x", False),  # plaintext to public IP
            ("ftp://host/x", False),
            ("", False),
            (None, False),
        ],
    )
    def test_server_url_allowed(self, url, ok):
        assert ocr_mineru._server_url_allowed(url) is ok

    def test_http_client_backend_refuses_unsafe_url(self, sample_pdf_path):
        do_parse = MagicMock()
        read_fn = MagicMock(return_value=b"%PDF-fake")
        with (
            _install_fake_mineru(do_parse, read_fn),
            patch.object(ocr_mineru, "MINERU_BACKEND", "vlm-http-client"),
            patch.object(ocr_mineru, "MINERU_SERVER_URL", "http://evil.example.com"),
        ):
            assert ocr_mineru._perform_mineru_ocr(sample_pdf_path) is None
        # Refused before importing mineru / reading the file.
        read_fn.assert_not_called()
        do_parse.assert_not_called()


class TestResourceCaps:
    """S3: untrusted documents are bounded by byte, page, and time caps."""

    def test_oversized_by_bytes_skips_parse(self, tmp_path):
        big = tmp_path / "big.pdf"
        big.write_bytes(b"x" * 2048)
        do_parse = MagicMock()
        read_fn = MagicMock(return_value=b"x" * 2048)
        with (
            _install_fake_mineru(do_parse, read_fn),
            patch.object(ocr_mineru, "MINERU_MAX_BYTES", 1024),
        ):
            assert ocr_mineru._perform_mineru_ocr(big) is None
        do_parse.assert_not_called()

    def test_oversized_by_pages_skips_parse(self, sample_pdf_path):
        do_parse = MagicMock()
        read_fn = MagicMock(return_value=b"%PDF-fake")
        with (
            _install_fake_mineru(do_parse, read_fn),
            patch.object(ocr_mineru, "MINERU_MAX_PAGES", 3),
            patch.object(ocr_mineru, "_pdf_page_count", return_value=5000),
        ):
            assert ocr_mineru._perform_mineru_ocr(sample_pdf_path) is None
        do_parse.assert_not_called()

    def test_within_caps_parses(self, sample_pdf_path):
        do_parse = _do_parse_writing_markdown("ok")
        read_fn = MagicMock(return_value=b"%PDF-fake")
        with (
            _install_fake_mineru(do_parse, read_fn),
            patch.object(ocr_mineru, "MINERU_MAX_BYTES", 10_000),
            patch.object(ocr_mineru, "MINERU_MAX_PAGES", 100),
            patch.object(ocr_mineru, "_pdf_page_count", return_value=2),
        ):
            assert ocr_mineru._perform_mineru_ocr(sample_pdf_path) == "ok"

    def test_parse_timeout_returns_none(self, sample_pdf_path):
        import time

        def _slow_do_parse(**kwargs):
            time.sleep(2.0)

        read_fn = MagicMock(return_value=b"%PDF-fake")
        with (
            _install_fake_mineru(_slow_do_parse, read_fn),
            patch.object(ocr_mineru, "MINERU_TIMEOUT_SECONDS", 0.1),
        ):
            assert ocr_mineru._perform_mineru_ocr(sample_pdf_path) is None


class TestConfigureMineruEnv:
    """Tests for _configure_mineru_env."""

    def test_sets_model_source_when_configured(self):
        with (
            patch.object(ocr_mineru, "MINERU_MODEL_SOURCE", "modelscope"),
            patch.dict(os.environ, {}, clear=True),
        ):
            ocr_mineru._configure_mineru_env()
            assert os.environ["MINERU_MODEL_SOURCE"] == "modelscope"

    def test_does_not_override_existing_env(self):
        with (
            patch.object(ocr_mineru, "MINERU_MODEL_SOURCE", "modelscope"),
            patch.dict(os.environ, {"MINERU_MODEL_SOURCE": "local"}),
        ):
            ocr_mineru._configure_mineru_env()
            assert os.environ["MINERU_MODEL_SOURCE"] == "local"

    def test_noop_when_not_configured(self):
        with (
            patch.object(ocr_mineru, "MINERU_MODEL_SOURCE", None),
            patch.dict(os.environ, {}, clear=True),
        ):
            ocr_mineru._configure_mineru_env()
            assert "MINERU_MODEL_SOURCE" not in os.environ
