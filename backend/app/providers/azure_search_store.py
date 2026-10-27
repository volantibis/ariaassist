"""Azure AI Search vector store, talking to the REST API directly with httpx.

API reference: Azure AI Search REST, api-version 2024-07-01 (GA, vector search included).
Auth: admin key from the SecretsProvider (secret name `azure-search-key`).
"""

from __future__ import annotations

from typing import Any

import httpx

from app.config import Settings
from app.providers.base import Chunk, SearchHit, SecretsProvider, VectorStore

API_VERSION = "2024-07-01"
VECTOR_FIELD = "vector"
PROFILE = "aria-profile"
BATCH = 500


def _odata_quote(value: str) -> str:
    return value.replace("'", "''")


class AzureSearchVectorStore(VectorStore):
    def __init__(
        self,
        settings: Settings,
        secrets: SecretsProvider,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._index = settings.azure_search_index
        self._client = client or httpx.AsyncClient(
            base_url=settings.azure_search_endpoint.rstrip("/"), timeout=httpx.Timeout(60.0)
        )
        self._secrets = secrets

    def _headers(self) -> dict[str, str]:
        return {"api-key": self._secrets.get("azure-search-key"), "Content-Type": "application/json"}

    async def _request(self, method: str, path: str, **kw: Any) -> httpx.Response:
        params = {"api-version": API_VERSION, **kw.pop("params", {})}
        return await self._client.request(
            method, path, params=params, headers=self._headers(), **kw
        )

    def _index_definition(self, dim: int) -> dict[str, Any]:
        return {
            "name": self._index,
            "fields": [
                {"name": "id", "type": "Edm.String", "key": True, "filterable": True},
                {"name": "text", "type": "Edm.String", "searchable": True},
                {"name": "source", "type": "Edm.String", "filterable": True},
                {"name": "page", "type": "Edm.Int32", "filterable": True},
                {"name": "kind", "type": "Edm.String", "filterable": True},
                {
                    "name": VECTOR_FIELD,
                    "type": "Collection(Edm.Single)",
                    "searchable": True,
                    "dimensions": dim,
                    "vectorSearchProfile": PROFILE,
                },
            ],
            "vectorSearch": {
                "algorithms": [
                    {"name": "hnsw-cosine", "kind": "hnsw", "hnswParameters": {"metric": "cosine"}}
                ],
                "profiles": [{"name": PROFILE, "algorithm": "hnsw-cosine"}],
            },
        }

    async def ensure_index(self, dim: int, *, recreate: bool = False) -> None:
        resp = await self._request("GET", f"/indexes/{self._index}")
        if resp.status_code == 200:
            fields = {f["name"]: f for f in resp.json().get("fields", [])}
            old_dim = fields.get(VECTOR_FIELD, {}).get("dimensions")
            if old_dim == dim and not recreate:
                return
            if old_dim != dim and not recreate:
                raise ValueError(
                    f"Azure Search index has dim {old_dim} but the embedder produces {dim}. "
                    "Recreate the index and re-embed (ingest.pipeline.reindex_all)."
                )
            (await self._request("DELETE", f"/indexes/{self._index}")).raise_for_status()
        elif resp.status_code != 404:
            resp.raise_for_status()
        created = await self._request("PUT", f"/indexes/{self._index}", json=self._index_definition(dim))
        created.raise_for_status()

    async def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        for start in range(0, len(chunks), BATCH):
            docs = [
                {
                    "@search.action": "mergeOrUpload",
                    "id": c.id,
                    "text": c.text,
                    "source": c.source,
                    "page": c.page if c.page is not None else -1,
                    "kind": c.kind,
                    VECTOR_FIELD: v,
                }
                for c, v in zip(chunks[start : start + BATCH], vectors[start : start + BATCH], strict=True)
            ]
            resp = await self._request("POST", f"/indexes/{self._index}/docs/index", json={"value": docs})
            resp.raise_for_status()

    async def search(self, vector: list[float], k: int = 4) -> list[SearchHit]:
        body = {
            "select": "id,text,source,page,kind",
            "top": k,
            "vectorQueries": [{"kind": "vector", "vector": vector, "fields": VECTOR_FIELD, "k": k}],
        }
        resp = await self._request("POST", f"/indexes/{self._index}/docs/search", json=body)
        resp.raise_for_status()
        hits = []
        for row in resp.json().get("value", []):
            page = row.get("page")
            chunk = Chunk(
                id=row["id"],
                text=row["text"],
                source=row["source"],
                page=page if page is not None and page >= 0 else None,
                kind=row.get("kind", "text"),
            )
            # For cosine, @search.score is already 0..1 (higher is better).
            hits.append(SearchHit(chunk=chunk, score=float(row.get("@search.score", 0.0))))
        return hits

    async def delete_source(self, source: str) -> None:
        body = {
            "search": "*",
            "filter": f"source eq '{_odata_quote(source)}'",
            "select": "id",
            "top": 1000,
        }
        resp = await self._request("POST", f"/indexes/{self._index}/docs/search", json=body)
        resp.raise_for_status()
        ids = [r["id"] for r in resp.json().get("value", [])]
        if ids:
            actions = [{"@search.action": "delete", "id": i} for i in ids]
            (
                await self._request("POST", f"/indexes/{self._index}/docs/index", json={"value": actions})
            ).raise_for_status()

    async def aclose(self) -> None:
        await self._client.aclose()
