# Document Sources Plugin

Official BaselithCore plugin for document ingestion sources.

## Capabilities

- filesystem source
- dynamic web source
- OCR backends (MinerU primary, Tesseract fallback)
- file readers for PDF, Office files and images

## Compatibility

Legacy imports from `core.doc_sources.*` are temporarily preserved through module-level shims during the migration away from the core.

## Safety limits

- **Filesystem containment**: `read_item` resolves the path (symlinks
  included) and requires it to be *inside* the root by path components —
  a sibling directory sharing the root's name prefix (`docs-private` next to
  `docs`) is refused.
- **Office archives**: `.docx`, `.xlsx` and `.pptx` are zip files the parsers
  inflate into memory. Before parsing, the central directory is screened:
  at most 10,000 members, 512 MiB uncompressed in total, and no member over
  1 MiB with a compression ratio above 1000:1. A refused file is logged and
  skipped.
- **Web source**: the httpx fallback uses the SSRF-hardened client from
  `core.security.http`, which pins every request (each redirect hop included)
  to the verified IP — no second DNS lookup for a rebinding record. Bodies
  are streamed and refused above 10 MB (`WEB_DOCUMENTS_MAX_BODY_BYTES`).
  The Playwright path installs a route guard that screens **every** browser
  request (redirects, scripts, images, fetch/XHR), not only the navigation.
  HTML parsing runs in a worker thread.
