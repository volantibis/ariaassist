"""Turn uploaded bytes into page-level text. No chunking and no embedding here."""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pypdf import PdfReader

Kind = Literal["pdf", "text", "image"]

TEXT_EXTS = {".txt", ".md"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
IMAGE_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


@dataclass
class PageText:
    page: int | None  # 1-based page number, None for formats without pages
    text: str


class UnsupportedFileError(ValueError):
    pass


def detect_kind(filename: str) -> Kind:
    """Classify an upload by extension. Raises UnsupportedFileError otherwise."""
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        return "pdf"
    if ext in TEXT_EXTS:
        return "text"
    if ext in IMAGE_EXTS:
        return "image"
    raise UnsupportedFileError(f"unsupported file type '{ext}'. Use PDF, TXT, MD, PNG, JPG or WEBP")


def image_mime(filename: str) -> str:
    return IMAGE_MIME[Path(filename).suffix.lower()]


def load_pdf(data: bytes) -> list[PageText]:
    """Extract text per page. Scanned PDFs with no text layer yield no pages."""
    reader = PdfReader(io.BytesIO(data))
    pages = []
    for i, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            pages.append(PageText(page=i, text=text))
    return pages


def load_text(data: bytes) -> list[PageText]:
    text = data.decode("utf-8", errors="replace").strip()
    return [PageText(page=None, text=text)] if text else []
