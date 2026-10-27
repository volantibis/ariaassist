"""Chroma vector store running embedded in the API process (local development).

Chroma's client is synchronous, so every call runs in a worker thread.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.providers.base import Chunk, SearchHit, VectorStore

COLLECTION = "aria"


class ChromaVectorStore(VectorStore):
    def __init__(self, path: str) -> None:
        import chromadb

        self._client = chromadb.PersistentClient(path=path)
        self._col: Any = None

    # -- helpers -----------------------------------------------------------
    def _ensure(self, dim: int, recreate: bool) -> None:
        existing = {c if isinstance(c, str) else c.name for c in self._client.list_collections()}
        if COLLECTION in existing:
            col = self._client.get_collection(COLLECTION)
            old_dim = (col.metadata or {}).get("dim")
            if old_dim == dim and not recreate:
                self._col = col
                return
            if old_dim != dim and not recreate:
                raise ValueError(
                    f"Chroma index has dim {old_dim} but the embedder produces {dim}. "
                    "Re-embed and recreate the index (see ingest.pipeline.reindex_all)."
                )
            self._client.delete_collection(COLLECTION)
        self._col = self._client.create_collection(
            COLLECTION, metadata={"hnsw:space": "cosine", "dim": dim}
        )

    def _upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        self._col.upsert(
            ids=[c.id for c in chunks],
            embeddings=vectors,
            documents=[c.text for c in chunks],
            metadatas=[
                {"source": c.source, "page": c.page if c.page is not None else -1, "kind": c.kind}
                for c in chunks
            ],
        )

    def _search(self, vector: list[float], k: int) -> list[SearchHit]:
        if self._col.count() == 0:
            return []
        res = self._col.query(query_embeddings=[vector], n_results=min(k, self._col.count()))
        hits: list[SearchHit] = []
        for cid, doc, meta, dist in zip(
            res["ids"][0], res["documents"][0], res["metadatas"][0], res["distances"][0], strict=True
        ):
            page = meta["page"] if meta["page"] >= 0 else None
            chunk = Chunk(id=cid, text=doc, source=meta["source"], page=page, kind=meta["kind"])
            hits.append(SearchHit(chunk=chunk, score=1.0 - float(dist)))  # cosine similarity
        return hits

    # -- VectorStore -------------------------------------------------------
    async def ensure_index(self, dim: int, *, recreate: bool = False) -> None:
        await asyncio.to_thread(self._ensure, dim, recreate)

    async def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        await asyncio.to_thread(self._upsert, chunks, vectors)

    async def search(self, vector: list[float], k: int = 4) -> list[SearchHit]:
        if self._col is None:
            return []
        return await asyncio.to_thread(self._search, vector, k)

    async def delete_source(self, source: str) -> None:
        if self._col is not None:
            await asyncio.to_thread(self._col.delete, where={"source": source})
