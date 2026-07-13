"""
Processing configuration.

Document ingestion, Web Crawling, OCR, and NLP settings.
"""

import logging
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

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
    documents_extensions: tuple[str, ...] = Field(
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
    web_documents_urls: list[str] = Field(
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
    web_documents_allowlist: list[str] = Field(
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
        default="mineru", alias="PDF_OCR_BACKEND"
    )
    mineru_backend: Literal[
        "pipeline",
        "vlm-engine",
        "hybrid-engine",
        "vlm-http-client",
        "hybrid-http-client",
    ] = Field(default="pipeline", alias="MINERU_BACKEND")
    mineru_lang: str = Field(default="en", alias="MINERU_LANG")
    mineru_formula_enable: bool = Field(default=True, alias="MINERU_FORMULA_ENABLE")
    mineru_table_enable: bool = Field(default=True, alias="MINERU_TABLE_ENABLE")
    mineru_server_url: str | None = Field(default=None, alias="MINERU_SERVER_URL")
    mineru_model_source: Literal["huggingface", "modelscope", "local"] | None = Field(
        default=None, alias="MINERU_MODEL_SOURCE"
    )

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
