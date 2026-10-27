"""Shared fakes. No test touches Ollama, Azure or the network."""

from __future__ import annotations

import zlib
from collections.abc import Callable
from typing import Any

import pytest

from app.agent.graph import build_graph
from app.agent.state import AgentDeps
from app.config import Settings
from app.db import Database
from app.providers.base import ChatMessage, LLMProvider, LLMResult
from app.providers.memory_store import MemoryVectorStore

DIM = 64


def hash_embed(text: str) -> list[float]:
    """Bag-of-words hashing embedder: similar words give similar vectors. Deterministic."""
    vec = [0.0] * DIM
    for token in text.lower().replace("?", " ").replace(".", " ").split():
        vec[zlib.crc32(token.encode()) % DIM] += 1.0
    return vec


class FakeLLM(LLMProvider):
    """Scriptable LLM. `script` maps the first schema property to the dict to return."""

    embedding_dim = DIM

    def __init__(self) -> None:
        self.script: dict[str, Any] = {}
        self.chat_text: str | Callable[[list[ChatMessage]], str] = "fake reply"
        self.fail = False
        self.chat_calls: list[list[ChatMessage]] = []
        self.structured_calls: list[str] = []

    async def chat(self, messages, tools=None):
        self.chat_calls.append(messages)
        if self.fail:
            raise RuntimeError("model offline")
        text = self.chat_text(messages) if callable(self.chat_text) else self.chat_text
        return LLMResult(text=text)

    async def structured(self, messages, schema):
        key = next(iter(schema["properties"]))
        self.structured_calls.append(key)
        if self.fail:
            raise RuntimeError("model offline")
        value = self.script.get(key)
        if isinstance(value, Exception):
            raise value
        return value if value is not None else {}

    async def embed(self, texts, *, is_query):
        return [hash_embed(t) for t in texts]

    async def caption_image(self, image_bytes, mime_type):
        return "A photo of a restaurant menu. Text: Pasta 450, Pizza 500"

    async def verify_models(self) -> None:
        return None


@pytest.fixture
def llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        vector_provider="memory",
        sqlite_path=":memory:",
        upload_dir=str(tmp_path / "uploads"),
        retrieval_min_score=0.05,
        proactive_every_n=2,
        proactive_cooldown_s=0,
    )


@pytest.fixture
def db() -> Database:
    return Database(":memory:")


@pytest.fixture
def store() -> MemoryVectorStore:
    return MemoryVectorStore()


@pytest.fixture
def deps(llm, store, db, settings) -> AgentDeps:
    return AgentDeps(llm=llm, store=store, db=db, settings=settings)


@pytest.fixture
def graph(deps):
    return build_graph(deps)


def make_state(text: str, *, trigger: str = "mention", history: list[dict] | None = None, requester: str = "bob") -> dict:
    history = history if history is not None else [{"role": "user", "content": text, "name": "bob"}]
    return {"trigger": trigger, "requester": requester, "history": history, "query": text}


def config(thread: str = "t1") -> dict:
    return {"configurable": {"thread_id": thread}}


def chat_history(*lines: str) -> list[dict]:
    """'bob: hi' style lines -> history dicts."""
    out = []
    for line in lines:
        name, text = line.split(": ", 1)
        out.append({"role": "user", "content": text, "name": name})
    return out
