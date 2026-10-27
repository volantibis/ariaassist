import base64
import io
import json

import httpx
import pytest
from PIL import Image

from app.config import Settings
from app.providers.base import ChatMessage, ToolSpec
from app.providers.factory import make_llm, make_secrets
from app.providers.ollama import OllamaProvider, strip_thinking


def make_provider(handler) -> OllamaProvider:
    client = httpx.AsyncClient(
        base_url="http://ollama.test", transport=httpx.MockTransport(handler)
    )
    return OllamaProvider(Settings(_env_file=None), client=client)


def test_strip_thinking():
    assert strip_thinking("<think>\nplanning\n</think>\nAnswer") == "Answer"
    assert strip_thinking("plain") == "plain"


def test_defaults_and_factory():
    s = Settings(_env_file=None)
    assert s.llm_provider == "ollama"
    assert isinstance(make_llm(s, make_secrets(s)), OllamaProvider)


async def test_embed_adds_task_prefixes():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(json.loads(req.content))
        return httpx.Response(200, json={"embeddings": [[0.1, 0.2]]})

    p = make_provider(handler)
    await p.embed(["cats"], is_query=True)
    assert seen["input"] == ["search_query: cats"]
    await p.embed(["cats"], is_query=False)
    assert seen["input"] == ["search_document: cats"]


async def test_chat_parses_tool_calls_and_disables_thinking():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(json.loads(req.content))
        return httpx.Response(
            200,
            json={
                "message": {
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "book_table", "arguments": {"party_size": 4}}}
                    ],
                }
            },
        )

    p = make_provider(handler)
    tool = ToolSpec(name="book_table", description="Book", parameters={"type": "object"})
    res = await p.chat([ChatMessage(role="user", content="book", name="bob")], tools=[tool])
    assert res.tool_calls[0].name == "book_table"
    assert res.tool_calls[0].arguments == {"party_size": 4}
    assert seen["think"] is False
    assert seen["messages"][0]["content"] == "bob: book"
    assert seen["tools"][0]["function"]["name"] == "book_table"


async def test_structured_retries_once_then_succeeds():
    replies = iter(["not json", '{"intent": "chat"}'])

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"content": next(replies)}})

    p = make_provider(handler)
    out = await p.structured([ChatMessage(role="user", content="x")], {"type": "object"})
    assert out == {"intent": "chat"}


async def test_structured_raises_after_two_failures():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"content": "nope"}})

    p = make_provider(handler)
    with pytest.raises(ValueError):
        await p.structured([ChatMessage(role="user", content="x")], {"type": "object"})


def make_png(size: tuple[int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size).save(buffer, format="PNG")
    return buffer.getvalue()


async def test_caption_image_resizes_oversized_image():
    seen: dict[str, object] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(json.loads(req.content))
        with Image.open(io.BytesIO(base64.b64decode(seen["messages"][0]["images"][0]))) as img:
            seen["size"] = img.size
        return httpx.Response(200, json={"message": {"content": "A hill."}})

    provider = make_provider(handler)
    big = make_png((2000, 1500))
    assert await provider.caption_image(big, "image/png") == "A hill."
    assert seen["model"] == provider._s.ollama_vision_model
    # 2000x1500 -> 1024x768: longest side capped, aspect ratio kept.
    assert seen["size"] == (1024, 768)


async def test_caption_image_passes_small_image_through_unchanged():
    original = make_png((300, 200))
    provider = make_provider(lambda req: httpx.Response(200, json={"message": {"content": "ok"}}))
    assert await provider.caption_image(original, "image/png") == "ok"

    # Verify byte-identical pass-through with a second handler that inspects the request.
    forwarded: dict[str, object] = {}

    def inspect(req: httpx.Request) -> httpx.Response:
        forwarded.update(json.loads(req.content))
        return httpx.Response(200, json={"message": {"content": "ok"}})

    provider = make_provider(inspect)
    await provider.caption_image(original, "image/png")
    assert base64.b64decode(forwarded["messages"][0]["images"][0]) == original


async def test_caption_image_rejects_unreadable_file():
    provider = make_provider(lambda req: httpx.Response(200, json={"message": {"content": "x"}}))
    with pytest.raises(ValueError, match="could not read image"):
        await provider.caption_image(b"not an image at all", "image/png")
