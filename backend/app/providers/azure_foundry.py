"""LLMProvider for Azure AI Foundry / Azure OpenAI deployments (cloud).

Talks to the OpenAI-compatible deployment endpoints:
  {endpoint}/openai/deployments/{deployment}/chat/completions?api-version=...
  {endpoint}/openai/deployments/{deployment}/embeddings?api-version=...
Auth: API key from the SecretsProvider (secret name `azure-foundry-api-key`).
The chat deployment must support tool calling, JSON-schema output and image input
(a gpt-4o-mini class deployment does).
"""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx

from app.config import Settings
from app.providers.base import (
    ChatMessage,
    LLMProvider,
    LLMResult,
    SecretsProvider,
    ToolCall,
    ToolSpec,
)


class AzureFoundryProvider(LLMProvider):
    def __init__(
        self,
        settings: Settings,
        secrets: SecretsProvider,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._s = settings
        self._secrets = secrets
        self.embedding_dim = settings.azure_embed_dim
        self._client = client or httpx.AsyncClient(
            base_url=settings.azure_foundry_endpoint.rstrip("/"), timeout=httpx.Timeout(120.0)
        )

    async def _post(self, deployment: str, op: str, payload: dict[str, Any]) -> dict[str, Any]:
        resp = await self._client.post(
            f"/openai/deployments/{deployment}/{op}",
            params={"api-version": self._s.azure_foundry_api_version},
            headers={"api-key": self._secrets.get("azure-foundry-api-key")},
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _to_wire(messages: list[ChatMessage]) -> list[dict[str, Any]]:
        out = []
        for m in messages:
            content = f"{m.name}: {m.content}" if m.name and m.role == "user" else m.content
            out.append({"role": m.role, "content": content})
        return out

    async def chat(
        self, messages: list[ChatMessage], tools: list[ToolSpec] | None = None
    ) -> LLMResult:
        payload: dict[str, Any] = {"messages": self._to_wire(messages), "temperature": 0.3}
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
                }
                for t in tools
            ]
        data = await self._post(self._s.azure_foundry_chat_deployment, "chat/completions", payload)
        msg = data["choices"][0]["message"]
        calls = [
            ToolCall(name=tc["function"]["name"], arguments=json.loads(tc["function"]["arguments"] or "{}"))
            for tc in msg.get("tool_calls") or []
        ]
        return LLMResult(text=(msg.get("content") or "").strip(), tool_calls=calls)

    async def structured(
        self, messages: list[ChatMessage], schema: dict[str, Any]
    ) -> dict[str, Any]:
        last_err: Exception | None = None
        for _ in range(2):
            payload = {
                "messages": self._to_wire(messages),
                "temperature": 0,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "output", "schema": schema},
                },
            }
            data = await self._post(self._s.azure_foundry_chat_deployment, "chat/completions", payload)
            raw = (data["choices"][0]["message"].get("content") or "").strip()
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    return parsed
                last_err = ValueError("structured output is not a JSON object")
            except json.JSONDecodeError as e:
                last_err = e
        raise ValueError(f"invalid structured output after retry: {last_err}")

    async def embed(self, texts: list[str], *, is_query: bool) -> list[list[float]]:
        data = await self._post(
            self._s.azure_foundry_embed_deployment, "embeddings", {"input": texts}
        )
        rows = sorted(data["data"], key=lambda r: r["index"])
        return [r["embedding"] for r in rows]

    async def caption_image(self, image_bytes: bytes, mime_type: str) -> str:
        b64 = base64.b64encode(image_bytes).decode()
        payload = {
            "temperature": 0,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Describe this image in 2-3 sentences, then transcribe any "
                                "visible text exactly under the heading 'Text:'."
                            ),
                        },
                        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64}"}},
                    ],
                }
            ],
        }
        data = await self._post(self._s.azure_foundry_chat_deployment, "chat/completions", payload)
        return (data["choices"][0]["message"].get("content") or "").strip()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def verify_models(self) -> None:
        """Azure Foundry validates deployments at request time; nothing to check up front."""
        return None
