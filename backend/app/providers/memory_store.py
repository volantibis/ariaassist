"""In-process vector store. Used by tests and the eval harness (no server, no files)."""

from __future__ import annotations

import math

from app.providers.base import Chunk, SearchHit, VectorStore


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class MemoryVectorStore(VectorStore):
    def __init__(self) -> None:
        self._dim: int | None = None
        self._rows: dict[str, tuple[Chunk, list[float]]] = {}

    async def ensure_index(self, dim: int, *, recreate: bool = False) -> None:
        if self._dim is not None and self._dim != dim and not recreate:
            raise ValueError(f"index has dim {self._dim}, embedder produces {dim}; recreate it")
        if recreate or self._dim != dim:
            self._rows.clear()
        self._dim = dim

    async def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        for c, v in zip(chunks, vectors, strict=True):
            self._rows[c.id] = (c, v)

    async def search(self, vector: list[float], k: int = 4) -> list[SearchHit]:
        scored = [SearchHit(chunk=c, score=_cosine(vector, v)) for c, v in self._rows.values()]
        return sorted(scored, key=lambda h: h.score, reverse=True)[:k]

    async def delete_source(self, source: str) -> None:
        for cid in [i for i, (c, _) in self._rows.items() if c.source == source]:
            del self._rows[cid]
