"""
Processing configuration.

Document ingestion, Web Crawling, OCR, and NLP settings.
"""

import logging
from typing import Annotated, Any, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from core.config._collections import csv_list

logger = logging.getLogger(__name__)


class ProcessingConfig(BaseSettings):
    """
    Processing configuration for ingestion pipelines.
    """

    model_config = SettingsConfigDict(
        case_sensitive=False,
        extra="ignore",
    )

    # === Documents ===
    # NoDecode + csv_list: ``.env.example`` documents
    # ``DOCUMENTS_EXTENSIONS=pdf,docx,txt,md``, and a tuple is a "complex" type
    # to pydantic-settings exactly like a list — so the documented value raised
    # a SettingsError out of the whole ProcessingConfig.
    documents_extensions: Annotated[tuple[str, ...], NoDecode] = Field(
        default=(
            ".md",
            ".markdown",
            ".pdf",
            ".docx",
            ".doc",
            ".xlsx",
            ".xls",
            ".pptx",
            ".ppt",
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".webp",
            ".tif",
            ".tiff",
        ),
        alias="DOCUMENTS_EXTENSIONS",
    )
    documents_root: str = Field(default="documents", alias="DOCUMENTS_ROOT")

    # === Web Crawling ===
    web_documents_enabled: bool = Field(default=False, alias="WEB_DOCUMENTS_ENABLED")
    # NoDecode + csv_list: ``.env.example`` ships this key blank, and a blank
    # value JSON-decodes to a SettingsError that takes the whole
    # ProcessingConfig — every document/OCR/NLP setting — down with it.
    web_documents_urls: Annotated[list[str], NoDecode] = Field(
        default_factory=list, alias="WEB_DOCUMENTS_URLS"
    )
    web_documents_max_pages: int = Field(
        default=5, alias="WEB_DOCUMENTS_MAX_PAGES", ge=1
    )
    web_documents_max_depth: int = Field(
        default=2, alias="WEB_DOCUMENTS_MAX_DEPTH", ge=1
    )
    web_documents_render_timeout: float = Field(
        default=20.0, alias="WEB_DOCUMENTS_RENDER_TIMEOUT", ge=1.0
    )
    web_documents_wait_selector: str | None = Field(
        default=None, alias="WEB_DOCUMENTS_WAIT_SELECTOR"
    )
    web_documents_user_agent: str = Field(
        default="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        alias="WEB_DOCUMENTS_USER_AGENT",
    )
    web_documents_allowlist: Annotated[list[str], NoDecode] = Field(
        default_factory=list, alias="WEB_DOCUMENTS_ALLOWLIST"
    )

    # === NLP / Spacy ===
    enable_spacy_documents: bool = Field(default=True, alias="ENABLE_SPACY_DOCUMENTS")
    spacy_model: str = Field(default="en_core_web_sm", alias="SPACY_MODEL")
    spacy_fallback_language: str | None = Field(
        default=None, alias="SPACY_FALLBACK_LANGUAGE"
    )

    # === OCR ===
    pdf_ocr_backend: Literal["auto", "mineru", "tesseract"] = Field(
        default="mineru",
        alias="PDF_OCR_BACKEND",
        description=(
            "OCR backend. MinerU is the default and needs the extra "
            '(pip install -e ".[mineru]") — heavy, and it downloads models on '
            "first use, so pre-fetch with `mineru-models-download`. Selected "
            "but not installed, OCR falls back to tesseract automatically; set "
            "'tesseract' to avoid pulling the mineru/transformers stack at all."
        ),
    )
    mineru_backend: Literal[
        "pipeline",
        "vlm-engine",
        "hybrid-engine",
        "vlm-http-client",
        "hybrid-http-client",
    ] = Field(
        default="pipeline",
        alias="MINERU_BACKEND",
        description="MinerU engine. 'pipeline' is the only CPU-friendly choice.",
    )
    mineru_lang: str = Field(
        default="en",
        alias="MINERU_LANG",
        description=(
            "OCR language model: 'en' and 'latin' cover Latin-script languages "
            "(Italian included); others are ch, korean, japan, th, el, arabic, "
            "cyrillic, devanagari, ..."
        ),
    )
    mineru_formula_enable: bool = Field(default=True, alias="MINERU_FORMULA_ENABLE")
    mineru_table_enable: bool = Field(default=True, alias="MINERU_TABLE_ENABLE")
    mineru_server_url: str | None = Field(
        default=None,
        alias="MINERU_SERVER_URL",
        description=(
            "Remote inference server for the *-http-client backends "
            "(e.g. http://mineru:30000)."
        ),
    )
    mineru_model_source: Literal["huggingface", "modelscope", "local"] | None = Field(
        default=None,
        alias="MINERU_MODEL_SOURCE",
        description=(
            "Model download source. Empty means 'auto' — the documented "
            "meaning of the blank value ``.env.example`` ships."
        ),
    )
    # Untrusted-document guards for the MinerU OCR path (documents may arrive
    # from the web crawler). 0 disables an individual cap.
    mineru_max_bytes: int = Field(
        default=50 * 1024 * 1024,
        ge=0,
        alias="MINERU_MAX_BYTES",
        description=(
            "Untrusted-document guards for the MinerU OCR path (documents may "
            "arrive from the web crawler). 0 disables an individual cap. "
            "Oversized or too-long input is skipped before the heavy parse; the "
            "parses themselves run on a dedicated bounded pool with a "
            "wall-clock timeout."
        ),
    )
    mineru_max_pages: int = Field(default=500, ge=0, alias="MINERU_MAX_PAGES")
    mineru_timeout_seconds: float = Field(
        default=300.0, ge=0, alias="MINERU_TIMEOUT_SECONDS"
    )
    mineru_max_concurrency: int = Field(default=2, ge=1, alias="MINERU_MAX_CONCURRENCY")

    @field_validator("mineru_model_source", mode="before")
    @classmethod
    def _blank_is_unset(cls, value: Any) -> Any:
        """Treat a blank value as "not configured", as the template documents.

        ``.env.example`` ships ``MINERU_MODEL_SOURCE=`` with the comment
        "empty = auto", but an empty string is not one of the Literal members —
        so copying the template rejected the whole ProcessingConfig.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("documents_extensions", mode="after")
    @classmethod
    def _normalise_extensions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Give every extension a leading dot and lowercase it.

        ``.env.example`` documents ``DOCUMENTS_EXTENSIONS=pdf,docx,txt,md``
        without dots, but the filesystem source matches against
        ``Path.suffix`` — which always carries one. A dotless entry therefore
        matches nothing, and since the value now parses cleanly the failure is
        silent: indexing simply finds zero documents. Accepting both spellings
        is cheaper than making the operator guess which one is meant.
        """
        return tuple(
            item if item.startswith(".") else f".{item}"
            for item in (raw.strip().lower() for raw in value)
            if item
        )

    @field_validator(
        "documents_extensions",
        "web_documents_urls",
        "web_documents_allowlist",
        mode="before",
    )
    @classmethod
    def _parse_csv_lists(cls, value: Any) -> Any:
        """Accept ``a,b`` and a blank value, as well as a JSON array.

        Paired with ``NoDecode`` on the fields — see
        :mod:`core.config._collections` for why both halves are needed.
        """
        return csv_list(value)

    @field_validator("pdf_ocr_backend", mode="before")
    @classmethod
    def _migrate_legacy_ocr_backend(cls, value: object) -> object:
        """Map the removed 'chandra' backend to 'mineru' instead of failing startup."""
        if isinstance(value, str) and value.strip().lower() == "chandra":
            logger.warning(
                "PDF_OCR_BACKEND='chandra' is no longer supported; using 'mineru'."
            )
            return "mineru"
        return value


# Global instance
_processing_config: ProcessingConfig | None = None


def get_processing_config() -> ProcessingConfig:
    """Get or create the global processing configuration instance."""
    global _processing_config
    if _processing_config is None:
        _processing_config = ProcessingConfig()
        logger.info(
            f"Initialized ProcessingConfig (web_enabled={_processing_config.web_documents_enabled})"
        )
    return _processing_config
