"""Ollama implementation of LLMProvider (local models)."""

from __future__ import annotations

import base64
import io
import json
import logging
import re
from typing import Any

import httpx
from PIL import Image

from app.config import Settings
from app.providers.base import (
    ChatMessage,
    LLMProvider,
    LLMResult,
    ModelNotFoundError,
    ToolCall,
    ToolSpec,
)

log = logging.getLogger("aria.ollama")

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def strip_thinking(text: str) -> str:
    """Remove Qwen3 <think>...</think> blocks if the server leaves them in."""
    return _THINK_RE.sub("", text).strip()


# Longest side (px) images are downscaled to before captioning. Ollama /
# qwen3-vl returns HTTP 500 for oversized images; capping keeps captions
# accurate while using far less memory on an 8 GB machine.
_MAX_IMAGE_SIDE = 1024


def _fit_image_for_caption(data: bytes, mime_type: str) -> tuple[bytes, str]:
    """Validate an uploaded image and downscale it if it is too large.

    Small images pass through unchanged. Returns (possibly re-encoded bytes,
    matching mime type). Raises ValueError if `data` is not a readable image.
    """
    try:
        img = Image.open(io.BytesIO(data))
        img.load()  # fail fast on corrupt or unrecognised files
    except Exception as exc:  # noqa: BLE001 - not an image we can caption
        raise ValueError(f"could not read image: {exc}") from exc
    width, height = img.size
    if max(width, height) <= _MAX_IMAGE_SIDE:
        return data, mime_type
    img = img.convert("RGB")  # JPEG cannot store alpha channels
    img.thumbnail((_MAX_IMAGE_SIDE, _MAX_IMAGE_SIDE), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=85)
    return buffer.getvalue(), "image/jpeg"


class OllamaProvider(LLMProvider):
    embedding_dim = 768  # nomic-embed-text v1.5

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self._s = settings
        self._client = client or httpx.AsyncClient(
            base_url=settings.ollama_base_url, timeout=httpx.Timeout(180.0)
        )

    async def _post(
        self, path: str, payload: dict[str, Any], *, model: str | None = None
    ) -> dict[str, Any]:
        try:
            resp = await self._client.post(path, json=payload)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404 and model:
                try:
                    detail = exc.response.json().get("error", "")
                except ValueError:
                    detail = ""
                if "not found" in detail.lower():
                    raise ModelNotFoundError(model, str(exc)) from exc
            raise

    async def _list_models(self) -> list[str]:
        """Return model names known to Ollama (GET /api/tags). Does not load models."""
        resp = await self._client.get("/api/tags")
        resp.raise_for_status()
        data = resp.json()
        return [m.get("name", "") for m in data.get("models", [])]

    async def _loaded_models(self) -> list[str]:
        """Return model names currently loaded in Ollama memory (GET /api/ps).

        This is what Ollama is actually serving right now - with
        OLLAMA_MAX_LOADED_MODELS=1 only one model is resident at a time.
        """
        try:
            resp = await self._client.get("/api/ps")
            resp.raise_for_status()
            data = resp.json()
            return [m.get("name", "") for m in data.get("models", [])]
        except Exception as exc:  # noqa: BLE001 - /api/ps may not exist on older Ollama
            log.debug("could not list loaded models: %s", exc)
            return []

    async def verify_models(self) -> None:
        """Fail fast at startup if chat / embed / vision models are missing from Ollama.

        Lists models via /api/tags (no model is loaded) and checks that every
        configured model name is present. This catches misconfigured model names
        before a user hits them at request time.
        """
        required = {
            "chat": self._s.ollama_chat_model,
            "embedding": self._s.ollama_embed_model,
            "vision": self._s.ollama_vision_model,
        }
        available = await self._list_models()
        loaded = await self._loaded_models()
        log.info(
            "Ollama configured models: chat=%s embedding=%s vision=%s; loaded in memory: %s",
            self._s.ollama_chat_model,
            self._s.ollama_embed_model,
            self._s.ollama_vision_model,
            loaded or "(none)",
        )
        missing: list[str] = []
        for _label, name in required.items():
            if name in available:
                continue
            if not any(a.startswith(name + ":") for a in available):
                missing.append(name)
        if missing:
            listed = ", ".join(sorted(available)) or "(none)"
            raise ModelNotFoundError(
                ", ".join(missing),
                f"Ollama model(s) not found: {', '.join(missing)}. "
                f"Available: {listed}. Run: ollama pull {' '.join(missing)}",
            )

    @staticmethod
    def _to_wire(messages: list[ChatMessage]) -> list[dict[str, Any]]:
        wire = []
        for m in messages:
            content = f"{m.name}: {m.content}" if m.name and m.role == "user" else m.content
            wire.append({"role": m.role, "content": content})
        return wire

    async def chat(
        self, messages: list[ChatMessage], tools: list[ToolSpec] | None = None
    ) -> LLMResult:
        log.debug("Ollama chat model=%s", self._s.ollama_chat_model)
        payload: dict[str, Any] = {
            "model": self._s.ollama_chat_model,
            "messages": self._to_wire(messages),
            "stream": False,
            "think": False,  # skip Qwen3 reasoning: faster, and we want short replies
            "options": {"temperature": 0.3},
        }
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]
        data = await self._post("/api/chat", payload, model=self._s.ollama_chat_model)
        msg = data.get("message", {})
        calls = [
            ToolCall(
                name=tc["function"]["name"],
                arguments=tc["function"].get("arguments", {}) or {},
            )
            for tc in msg.get("tool_calls", []) or []
        ]
        return LLMResult(text=strip_thinking(msg.get("content", "")), tool_calls=calls)

    async def structured(
        self, messages: list[ChatMessage], schema: dict[str, Any]
    ) -> dict[str, Any]:
        log.debug("Ollama structured model=%s", self._s.ollama_chat_model)
        last_err: Exception | None = None
        for _ in range(2):  # one retry on bad JSON
            payload = {
                "model": self._s.ollama_chat_model,
                "messages": self._to_wire(messages),
                "stream": False,
                "think": False,
                "format": schema,
                "options": {"temperature": 0},
            }
            data = await self._post(
                "/api/chat", payload, model=self._s.ollama_chat_model
            )
            raw = strip_thinking(data.get("message", {}).get("content", ""))
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    return parsed
                last_err = ValueError("structured output is not a JSON object")
            except json.JSONDecodeError as e:
                last_err = e
        raise ValueError(f"invalid structured output after retry: {last_err}")

    async def embed(self, texts: list[str], *, is_query: bool) -> list[list[float]]:
        log.debug("Ollama embed model=%s", self._s.ollama_embed_model)
        prefix = "search_query: " if is_query else "search_document: "
        data = await self._post(
            "/api/embed",
            {
                "model": self._s.ollama_embed_model,
                "input": [prefix + t for t in texts],
            },
            model=self._s.ollama_embed_model,
        )
        return data["embeddings"]

    async def caption_image(self, image_bytes: bytes, mime_type: str) -> str:
        log.debug("Ollama vision model=%s", self._s.ollama_vision_model)
        image_bytes, mime_type = _fit_image_for_caption(image_bytes, mime_type)
        b64 = base64.b64encode(image_bytes).decode()
        data = await self._post(
            "/api/chat",
            {
                "model": self._s.ollama_vision_model,
                "stream": False,
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "Describe this image in 2-3 sentences, then transcribe any "
                            "visible text exactly under the heading 'Text:'."
                        ),
                        "images": [b64],
                    }
                ],
                "options": {"temperature": 0},
            },
            model=self._s.ollama_vision_model,
        )
        return strip_thinking(data.get("message", {}).get("content", ""))

    async def aclose(self) -> None:
        await self._client.aclose()
