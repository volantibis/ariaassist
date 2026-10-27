"""Upload -> text -> chunks -> embeddings -> vector store (+ a row in SQLite).

Images use "caption then embed": a vision model describes the image and transcribes
visible text; that text is chunked and embedded like any document.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.config import Settings
from app.db import Database, DocumentRow
from app.ingest.chunker import chunk_pages
from app.ingest.loaders import (
    Kind,
    PageText,
    UnsupportedFileError,
    detect_kind,
    image_mime,
    load_pdf,
    load_text,
)
from app.providers.base import Chunk, LLMProvider, VectorStore

EMBED_BATCH = 16


def safe_name(filename: str) -> str:
    """Strip any directory part so uploads cannot escape the upload dir."""
    name = Path(filename).name.strip()
    if not name or name.startswith("."):
        raise UnsupportedFileError("invalid file name")
    return name


async def build_chunks(llm: LLMProvider, filename: str, kind: Kind, data: bytes) -> list[Chunk]:
    """Extract text (or a caption for images) and split it into Chunks."""
    if kind == "pdf":
        return chunk_pages(load_pdf(data), filename, kind="text")
    if kind == "text":
        return chunk_pages(load_text(data), filename, kind="text")
    caption = await llm.caption_image(data, image_mime(filename))
    pages = [PageText(page=None, text=caption)] if caption.strip() else []
    return chunk_pages(pages, filename, kind="image_caption")


async def embed_and_store(
    llm: LLMProvider, store: VectorStore, chunks: list[Chunk], *, recreate: bool = False
) -> None:
    """Embed in small batches (8 GB machines) and write to the vector store."""
    await store.ensure_index(llm.embedding_dim, recreate=recreate)
    for start in range(0, len(chunks), EMBED_BATCH):
        batch = chunks[start : start + EMBED_BATCH]
        vectors = await llm.embed([c.text for c in batch], is_query=False)
        await store.upsert(batch, vectors)


async def ingest_file(
    *,
    llm: LLMProvider,
    store: VectorStore,
    db: Database,
    settings: Settings,
    filename: str,
    data: bytes,
    uploaded_by: str,
) -> DocumentRow:
    name = safe_name(filename)
    kind = detect_kind(name)
    chunks = await build_chunks(llm, name, kind, data)
    if not chunks:
        raise UnsupportedFileError("no extractable text found in this file")

    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    (upload_dir / name).write_bytes(data)

    await store.ensure_index(llm.embedding_dim)
    await store.delete_source(name)  # re-upload replaces the old version
    await embed_and_store(llm, store, chunks)

    doc = DocumentRow(
        id=uuid.uuid4().hex,
        name=name,
        kind=kind,
        chunks=len(chunks),
        uploaded_by=uploaded_by,
        ts=datetime.now(UTC).isoformat(),
    )
    await db.add_document(doc)
    return doc


async def reindex_all(
    *, llm: LLMProvider, store: VectorStore, db: Database, settings: Settings
) -> int:
    """Recreate the index and re-embed every stored upload.

    Run this after switching embedding models (e.g. local nomic 768-d -> Azure 1536-d),
    because vectors from different models are not comparable.
    Returns the number of chunks written.
    """
    await store.ensure_index(llm.embedding_dim, recreate=True)
    total = 0
    for doc in await db.list_documents():
        path = Path(settings.upload_dir) / doc.name
        if not path.exists():
            continue
        chunks = await build_chunks(llm, doc.name, doc.kind, path.read_bytes())  # type: ignore[arg-type]
        await embed_and_store(llm, store, chunks)
        total += len(chunks)
    return total
