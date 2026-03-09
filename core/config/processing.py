"""
Processing configuration.

Document ingestion, Web Crawling, OCR, and NLP settings.
"""

import logging
from typing import List, Optional, Literal, Tuple

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class ProcessingConfig(BaseSettings):
    """
    Processing configuration for ingestion pipelines.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # === Documents ===
    documents_extensions: Tuple[str, ...] = Field(
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
    web_documents_urls: List[str] = Field(
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
    web_documents_wait_selector: Optional[str] = Field(
        default=None, alias="WEB_DOCUMENTS_WAIT_SELECTOR"
    )
    web_documents_user_agent: str = Field(
        default="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        alias="WEB_DOCUMENTS_USER_AGENT",
    )
    web_documents_allowlist: List[str] = Field(
        default_factory=list, alias="WEB_DOCUMENTS_ALLOWLIST"
    )

    # === NLP / Spacy ===
    enable_spacy_documents: bool = Field(default=True, alias="ENABLE_SPACY_DOCUMENTS")
    spacy_model: str = Field(default="en_core_web_sm", alias="SPACY_MODEL")
    spacy_fallback_language: Optional[str] = Field(
        default=None, alias="SPACY_FALLBACK_LANGUAGE"
    )

    # === OCR ===
    pdf_ocr_backend: Literal["auto", "chandra", "tesseract"] = Field(
        default="chandra", alias="PDF_OCR_BACKEND"
    )
    chandra_ocr_method: Literal["vllm", "hf"] = Field(
        default="vllm", alias="CHANDRA_OCR_METHOD"
    )
    chandra_max_output_tokens: Optional[int] = Field(
        default=None, alias="CHANDRA_MAX_OUTPUT_TOKENS"
    )
    chandra_max_workers: Optional[int] = Field(
        default=None, alias="CHANDRA_MAX_WORKERS"
    )
    chandra_max_retries: Optional[int] = Field(
        default=None, alias="CHANDRA_MAX_RETRIES"
    )
    chandra_include_headers_footers: bool = Field(
        default=False, alias="CHANDRA_INCLUDE_HEADERS_FOOTERS"
    )

    chandra_vllm_api_base: Optional[str] = Field(
        default=None, alias="CHANDRA_VLLM_API_BASE"
    )
    chandra_vllm_api_key: Optional[str] = Field(
        default=None, alias="CHANDRA_VLLM_API_KEY"
    )
    chandra_vllm_model_name: Optional[str] = Field(
        default=None, alias="CHANDRA_VLLM_MODEL_NAME"
    )


# Global instance
_processing_config: Optional[ProcessingConfig] = None


def get_processing_config() -> ProcessingConfig:
    """Get or create the global processing configuration instance."""
    global _processing_config
    if _processing_config is None:
        _processing_config = ProcessingConfig()
        logger.info(
            f"Initialized ProcessingConfig (web_enabled={_processing_config.web_documents_enabled})"
        )
    return _processing_config
