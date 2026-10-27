"""Azure adapters against mocked HTTP: checks URLs, auth headers and payload shapes."""

import json

import httpx
import pytest

from app.config import Settings
from app.providers.azure_foundry import AzureFoundryProvider
from app.providers.azure_search_store import AzureSearchVectorStore
from app.providers.base import ChatMessage, Chunk, SecretsProvider, ToolSpec
from app.providers.factory import make_llm, make_secrets, make_vector_store
from app.providers.secrets_env import EnvSecrets


class StubSecrets(SecretsProvider):
    def get(self, name: str) -> str:
        return {"azure-foundry-api-key": "k-llm", "azure-search-key": "k-search"}[name]


def cfg(**kw) -> Settings:
    return Settings(
        _env_file=None,
        azure_foundry_endpoint="https://f.openai.azure.com",
        azure_foundry_chat_deployment="chat-dep",
        azure_foundry_embed_deployment="embed-dep",
        azure_search_endpoint="https://s.search.windows.net",
        azure_search_index="aria-docs",
        **kw,
    )


# ------------------------------------------------------------------ Foundry
def foundry(handler) -> AzureFoundryProvider:
    s = cfg()
    client = httpx.AsyncClient(base_url=s.azure_foundry_endpoint, transport=httpx.MockTransport(handler))
    return AzureFoundryProvider(s, StubSecrets(), client=client)


async def test_foundry_chat_url_auth_and_tool_calls():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"], seen["key"], seen["body"] = str(req.url), req.headers["api-key"], json.loads(req.content)
        msg = {"content": None, "tool_calls": [{"function": {"name": "book", "arguments": '{"n": 4}'}}]}
        return httpx.Response(200, json={"choices": [{"message": msg}]})

    p = foundry(handler)
    tool = ToolSpec(name="book", description="d", parameters={"type": "object"})
    res = await p.chat([ChatMessage("user", "hi", name="bob")], tools=[tool])
    assert seen["url"].startswith("https://f.openai.azure.com/openai/deployments/chat-dep/chat/completions")
    assert "api-version=2024-10-21" in seen["url"] and seen["key"] == "k-llm"
    assert seen["body"]["messages"][0]["content"] == "bob: hi"
    assert res.tool_calls[0].arguments == {"n": 4}


async def test_foundry_structured_sends_json_schema_and_retries():
    replies = iter(["oops", '{"intent": "summarize"}'])
    bodies = []

    def handler(req: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": next(replies)}}]})

    p = foundry(handler)
    out = await p.structured([ChatMessage("user", "x")], {"type": "object", "properties": {"intent": {}}})
    assert out == {"intent": "summarize"} and len(bodies) == 2
    assert bodies[0]["response_format"]["type"] == "json_schema"


async def test_foundry_embed_orders_by_index_and_caption_sends_data_url():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        if "embeddings" in str(req.url):
            seen["embed_url"] = str(req.url)
            data = [{"index": 1, "embedding": [2.0]}, {"index": 0, "embedding": [1.0]}]
            return httpx.Response(200, json={"data": data})
        seen["image"] = body["messages"][0]["content"][1]["image_url"]["url"]
        return httpx.Response(200, json={"choices": [{"message": {"content": "a cat"}}]})

    p = foundry(handler)
    assert await p.embed(["a", "b"], is_query=False) == [[1.0], [2.0]]
    assert "embed-dep/embeddings" in seen["embed_url"]
    assert await p.caption_image(b"img", "image/png") == "a cat"
    assert seen["image"].startswith("data:image/png;base64,")
    assert p.embedding_dim == 1536


# ------------------------------------------------------------ Azure Search
def search_store(handler) -> AzureSearchVectorStore:
    s = cfg()
    client = httpx.AsyncClient(base_url=s.azure_search_endpoint, transport=httpx.MockTransport(handler))
    return AzureSearchVectorStore(s, StubSecrets(), client=client)


async def test_search_creates_missing_index_with_vector_config():
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, req.url.path, json.loads(req.content) if req.content else None))
        assert req.headers["api-key"] == "k-search" and "api-version=2024-07-01" in str(req.url)
        return httpx.Response(404) if req.method == "GET" else httpx.Response(201, json={})

    await search_store(handler).ensure_index(768)
    method, path, body = calls[-1]
    assert (method, path) == ("PUT", "/indexes/aria-docs")
    vec = next(f for f in body["fields"] if f["name"] == "vector")
    assert vec["dimensions"] == 768 and vec["vectorSearchProfile"] == "aria-profile"
    assert body["vectorSearch"]["algorithms"][0]["hnswParameters"]["metric"] == "cosine"


async def test_search_dimension_mismatch_refused_unless_recreate():
    methods = []

    def handler(req: httpx.Request) -> httpx.Response:
        methods.append(req.method)
        if req.method == "GET":
            return httpx.Response(200, json={"fields": [{"name": "vector", "dimensions": 768}]})
        return httpx.Response(204 if req.method == "DELETE" else 201, json={})

    store = search_store(handler)
    with pytest.raises(ValueError, match="dim 768"):
        await store.ensure_index(1536)
    assert methods == ["GET"]
    await store.ensure_index(1536, recreate=True)
    assert methods[1:] == ["GET", "DELETE", "PUT"]
    await store.ensure_index(768)  # matches: no-op


async def test_search_upsert_query_and_delete_by_source():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        if req.url.path.endswith("/docs/index"):
            seen.setdefault("index", []).append(body["value"])
            return httpx.Response(200, json={})
        if "vectorQueries" in body:
            seen["query"] = body
            row = {"id": "c1", "text": "t", "source": "a.pdf", "page": 2, "kind": "text", "@search.score": 0.8}
            return httpx.Response(200, json={"value": [row]})
        seen["filter"] = body["filter"]
        return httpx.Response(200, json={"value": [{"id": "c1"}, {"id": "c2"}]})

    store = search_store(handler)
    await store.upsert([Chunk("c1", "t", "a.pdf", page=None)], [[0.1, 0.2]])
    doc = seen["index"][0][0]
    assert doc["@search.action"] == "mergeOrUpload" and doc["page"] == -1 and doc["vector"] == [0.1, 0.2]

    hits = await store.search([0.1, 0.2], k=3)
    assert seen["query"]["vectorQueries"][0]["k"] == 3
    assert hits[0].chunk.page == 2 and hits[0].score == 0.8

    await store.delete_source("o'brien.pdf")
    assert seen["filter"] == "source eq 'o''brien.pdf'"  # OData quote escaping
    assert [d["@search.action"] for d in seen["index"][-1]] == ["delete", "delete"]


# ------------------------------------------------------- secrets and factory
def test_env_secrets_maps_dashes_to_env_names(monkeypatch, tmp_path):
    monkeypatch.setenv("AZURE_SEARCH_KEY", "abc")
    monkeypatch.delenv("AZURE_FOUNDRY_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("AZURE_FOUNDRY_API_KEY=from-file\n")
    secrets = EnvSecrets(str(env_file))
    assert secrets.get("azure-search-key") == "abc"
    assert secrets.get("azure-foundry-api-key") == "from-file"
    with pytest.raises(KeyError, match="NOPE"):
        secrets.get("nope")


def test_factory_selects_providers():
    s = cfg(llm_provider="azure_foundry", vector_provider="azure_search")
    secrets = make_secrets(s)
    assert isinstance(make_llm(s, secrets), AzureFoundryProvider)
    assert isinstance(make_vector_store(s, secrets), AzureSearchVectorStore)
    assert type(make_vector_store(cfg(vector_provider="memory"), secrets)).__name__ == "MemoryVectorStore"


def test_env_example_is_valid_and_complete():
    """Every variable in .env.example is a real Settings field or a known secret."""
    from pathlib import Path

    example = Path(__file__).resolve().parents[2] / ".env.example"
    s = Settings(_env_file=str(example))
    assert s.llm_provider == "ollama" and s.proactive_every_n == 4
    assert s.cors_origin_list == ["http://localhost:5173", "http://127.0.0.1:5173"]

    names = {
        line.split("=", 1)[0].strip().lower()
        for line in example.read_text().splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    }
    secrets = {"azure_foundry_api_key", "azure_search_key"}
    assert names - secrets <= set(Settings.model_fields)
