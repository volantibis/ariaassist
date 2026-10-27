"""Provider interfaces. The agent and API layers depend ONLY on these.

Concrete implementations (ollama, azure_foundry, chroma, azure_search, env,
keyvault) live beside this file and are selected by providers/factory.py.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class ChatMessage:
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    name: str | None = None  # author name in group chat, or tool name


@dataclass
class ToolSpec:
    """Tool description in JSON-schema form (OpenAI-style function spec)."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResult:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


@dataclass
class Chunk:
    id: str
    text: str
    source: str  # file name or blob URL
    page: int | None = None
    kind: Literal["text", "image_caption"] = "text"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchHit:
    chunk: Chunk
    score: float


class ModelNotFoundError(Exception):
    """Raised when a configured model/deployment is not available on the provider."""

    def __init__(self, model: str, detail: str = ""):
        self.model = model
        self.detail = detail
        super().__init__(f"model not found: {model}" + (f" ({detail})" if detail else ""))


class LLMProvider(ABC):
    """Chat, structured output, embeddings, and image captioning."""

    @abstractmethod
    async def verify_models(self) -> None:
        """Check that configured models exist. No-op by default; Ollama overrides."""

    embedding_dim: int

    @abstractmethod
    async def chat(
        self, messages: list[ChatMessage], tools: list[ToolSpec] | None = None
    ) -> LLMResult: ...

    @abstractmethod
    async def structured(
        self, messages: list[ChatMessage], schema: dict[str, Any]
    ) -> dict[str, Any]:
        """Return a dict that validates against `schema`.

        Implementations retry once on invalid JSON, then raise ValueError.
        """

    @abstractmethod
    async def embed(self, texts: list[str], *, is_query: bool) -> list[list[float]]:
        """Embed texts. Providers that need task prefixes (nomic) add them here."""

    @abstractmethod
    async def caption_image(self, image_bytes: bytes, mime_type: str) -> str:
        """Describe the image and transcribe any visible text."""


class VectorStore(ABC):
    @abstractmethod
    async def ensure_index(self, dim: int, *, recreate: bool = False) -> None:
        """Create the index for `dim`-dimensional vectors. Recreate on dim change."""

    @abstractmethod
    async def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None: ...

    @abstractmethod
    async def search(self, vector: list[float], k: int = 4) -> list[SearchHit]: ...

    @abstractmethod
    async def delete_source(self, source: str) -> None: ...


class SecretsProvider(ABC):
    @abstractmethod
    def get(self, name: str) -> str:
        """Return the secret or raise KeyError."""
