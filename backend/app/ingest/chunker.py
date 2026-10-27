"""Split page text into overlapping chunks suitable for embedding."""

from __future__ import annotations

import hashlib

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.ingest.loaders import PageText
from app.providers.base import Chunk

CHUNK_SIZE = 800  # characters; nomic-embed handles far more, but small chunks cite better
CHUNK_OVERLAP = 100


def chunk_id(source: str, page: int | None, index: int) -> str:
    """Deterministic id so re-uploading the same file overwrites instead of duplicating."""
    raw = f"{source}|{page}|{index}".encode()
    return hashlib.sha1(raw).hexdigest()[:20]  # noqa: S324 - not security sensitive


def chunk_pages(
    pages: list[PageText],
    source: str,
    *,
    kind: str = "text",
    size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> list[Chunk]:
    splitter = RecursiveCharacterTextSplitter(chunk_size=size, chunk_overlap=overlap)
    chunks: list[Chunk] = []
    for p in pages:
        for i, piece in enumerate(splitter.split_text(p.text)):
            chunks.append(
                Chunk(
                    id=chunk_id(source, p.page, i),
                    text=piece,
                    source=source,
                    page=p.page,
                    kind=kind,  # type: ignore[arg-type]
                )
            )
    return chunks
