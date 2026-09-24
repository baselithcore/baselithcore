"""Optional Docling-based PDF reader for document ingestion.

The module is deliberately isolated from ``readers.py`` so importing the
document-sources plugin does not require Docling. When the optional dependency
is unavailable, callers can keep the legacy pypdf/OCR path.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.config import get_vectorstore_config
from core.observability.logging import get_logger

from .utils import normalize_text

logger = get_logger(__name__)

MONTH_RE = re.compile(
    r"^(?:fine\s+)?(?:gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|"
    r"agosto|settembre|ottobre|novembre|dicembre)(?:[- ](?:gennaio|febbraio|"
    r"marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|novembre|"
    r"dicembre))?$",
    re.I,
)
YEAR_RE = re.compile(r"^20\d{2}$")
DAY_RE = re.compile(r"^\d{1,2}$")


@dataclass(frozen=True)
class StructuredPDF:
    """Parsed PDF text plus chunk records ready for vector indexing."""

    content: str
    chunks: list[dict[str, Any]]
    metadata: dict[str, Any]


def clean_text(text: str) -> str:
    """Normalize Unicode and newlines without semantic rewriting."""
    return (
        unicodedata.normalize("NFC", text)
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .strip()
    )


def normalize_timeline(text: str) -> str:
    """Return simple ``date: event`` lines for visual timeline blocks.

    Some PDFs put the event before its date in the text stream. This conservative
    pass only fires when month/year or day/month/year lines are explicit and
    close to an accumulated event, making the relation easier for retrieval and
    answer generation without trying to infer arbitrary layout.
    """
    lines = [line.strip(" >") for line in text.splitlines() if line.strip(" >")]
    items: list[str] = []
    event_lines: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        date: str | None = None
        if MONTH_RE.match(line) and i + 1 < len(lines) and YEAR_RE.match(lines[i + 1]):
            date = f"{line} {lines[i + 1]}"
            i += 2
        elif (
            DAY_RE.match(line)
            and i + 2 < len(lines)
            and MONTH_RE.match(lines[i + 1])
            and YEAR_RE.match(lines[i + 2])
        ):
            date = f"{line} {lines[i + 1]} {lines[i + 2]}"
            i += 3
        else:
            event_lines.append(line)
            i += 1
            continue

        event = " ".join(event_lines).strip()
        if event and len(event) > 20:
            items.append(f"- {date}: {event}")
        event_lines = []

    return "\n".join(items[-8:])


def _short_heading(record: dict[str, Any]) -> str:
    headings = record.get("headings") or []
    return " > ".join(str(heading) for heading in headings if heading).strip()


def _embedding_text(record: dict[str, Any], filename: str) -> str:
    parts = [
        f"Documento: {filename}",
        "Pagina: " + ", ".join(map(str, record.get("pages") or ["non disponibile"])),
    ]
    heading = _short_heading(record)
    if heading:
        parts.append(f"Sezione: {heading}")
    parts.append(str(record.get("text") or ""))
    return clean_text("\n".join(parts))


def _context_text(
    records: list[dict[str, Any]],
    center_index: int,
    *,
    filename: str,
    budget_chars: int,
) -> str:
    """Build a compact same-page context window around a chunk."""
    center = records[center_index]
    center_pages = set(center.get("pages") or [])
    candidates = [
        idx
        for idx, record in enumerate(records)
        if center_pages and center_pages & set(record.get("pages") or [])
    ] or [center_index]
    if center_index not in candidates:
        candidates.append(center_index)
    candidates = sorted(candidates)
    position = candidates.index(center_index)
    selected = [center_index]
    left = position - 1
    right = position + 1
    while left >= 0 or right < len(candidates):
        added = False
        for idx in [idx for idx in (left, right) if 0 <= idx < len(candidates)]:
            trial = sorted(set(selected + [candidates[idx]]))
            body = "\n\n".join(
                str(records[item].get("embedding_text") or "") for item in trial
            )
            if len(body) <= budget_chars:
                selected.append(candidates[idx])
                added = True
        left -= 1
        right += 1
        if (
            not added
            and len(
                "\n\n".join(
                    str(records[item].get("embedding_text") or "") for item in selected
                )
            )
            >= budget_chars * 0.8
        ):
            break

    selected = sorted(set(selected))
    pages = sorted({page for idx in selected for page in records[idx].get("pages", [])})
    heading = _short_heading(center)
    header = [
        f"Documento: {filename}",
        f"Pagine: {', '.join(map(str, pages)) or 'non disponibile'}",
    ]
    if heading:
        header.append(f"Sezione: {heading}")
    body = "\n\n".join(
        str(records[idx].get("embedding_text") or "") for idx in selected
    )
    timeline = (
        normalize_timeline(body)
        if "CRONOLOGIA" in body.upper() or "TIMELINE" in body.upper()
        else ""
    )
    if timeline:
        body = (
            "TIMELINE NORMALIZZATA EVENTO-DATA:\n"
            + timeline
            + "\n\nTESTO ORIGINALE:\n"
            + body
        )
    return clean_text("\n".join(header) + "\n\n" + body)


def _docling_chunks(
    document: Any, *, filename: str, target_tokens: int
) -> list[dict[str, Any]]:
    from docling.chunking import HybridChunker
    from docling_core.transforms.chunker.hierarchical_chunker import (
        ChunkingSerializerProvider,
    )
    from docling_core.transforms.chunker.tokenizer.huggingface import (
        HuggingFaceTokenizer,
    )
    from transformers import AutoTokenizer

    class IncludingFigureText(ChunkingSerializerProvider):
        def get_serializer(self, doc: Any) -> Any:
            serializer = super().get_serializer(doc)
            serializer.params.traverse_pictures = True
            return serializer

    tokenizer = AutoTokenizer.from_pretrained(  # nosec B615 - operator-configured model; Docker builds prefetch the approved default.
        get_vectorstore_config().embedding_model
    )
    chunker = HybridChunker(
        serializer_provider=IncludingFigureText(),
        tokenizer=HuggingFaceTokenizer(tokenizer=tokenizer, max_tokens=target_tokens),
        merge_peers=True,
    )

    records: list[dict[str, Any]] = []
    for index, chunk in enumerate(chunker.chunk(dl_doc=document)):
        text = clean_text(chunk.text)
        if not text:
            continue
        provenance = [
            {
                "ref": element.self_ref,
                "page": page.page_no,
                "bbox": page.bbox.model_dump(mode="json"),
            }
            for element in chunk.meta.doc_items
            for page in element.prov
        ]
        record = {
            "source_index": index,
            "text": text,
            "pages": sorted({item["page"] for item in provenance}),
            "headings": list(chunk.meta.headings or []),
            "provenance": provenance,
            "parser": "docling",
        }
        record["embedding_text"] = clean_text(chunker.contextualize(chunk=chunk))
        record["embedding_text"] = _embedding_text(record, filename)
        records.append(record)
    return records


def read_pdf_with_docling(
    path: Path, *, target_tokens: int = 240, context_tokens: int = 520
) -> StructuredPDF | None:
    """Parse a PDF with Docling and return structured chunk metadata.

    Raises ImportError when Docling is not installed so callers can decide
    whether to fall back or fail based on configuration.
    """
    from docling.datamodel.base_models import ConversionStatus, InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    options = PdfPipelineOptions()
    options.do_ocr = True
    options.do_table_structure = True
    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
    )
    result = converter.convert(path)
    if result.status != ConversionStatus.SUCCESS:
        logger.warning(
            "[filesystem] Docling failed for PDF %s: %s", path, result.status
        )
        return None

    document = result.document
    try:
        content = clean_text(document.export_to_markdown())
    except Exception:
        content = ""
    if not content:
        texts = [
            clean_text(getattr(item, "text", ""))
            for item in getattr(document, "texts", [])
        ]
        content = clean_text("\n\n".join(text for text in texts if text))
    if not content:
        return None

    records = _docling_chunks(document, filename=path.name, target_tokens=target_tokens)
    if not records:
        return StructuredPDF(
            content=normalize_text(content) or content,
            chunks=[],
            metadata={"pdf_reader": "docling", "parser": "docling"},
        )

    # The notebook uses token budgets; here we translate to a conservative
    # character budget so the reader does not depend on the embedder runtime.
    budget_chars = max(1200, context_tokens * 4)
    for index, record in enumerate(records):
        context = _context_text(
            records, index, filename=path.name, budget_chars=budget_chars
        )
        record["context_text"] = context
        record["chunk_body"] = context
        record["original_text"] = record["text"]
        record["text"] = context

    return StructuredPDF(
        content=normalize_text(content) or content,
        chunks=records,
        metadata={
            "pdf_reader": "docling",
            "parser": "docling",
            "docling_chunk_count": len(records),
        },
    )


__all__ = [
    "StructuredPDF",
    "clean_text",
    "normalize_timeline",
    "read_pdf_with_docling",
]
