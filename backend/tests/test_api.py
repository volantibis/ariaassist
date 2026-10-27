"""End-to-end through the real FastAPI app, WebSocket protocol and service (fake LLM)."""

import asyncio
import io
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from starlette.websockets import WebSocketDisconnect

from app.db import BookingRow, Database
from app.main import create_app
from app.providers.memory_store import MemoryVectorStore
from tests.conftest import FakeLLM


@pytest.fixture
def make_client(llm, settings):
    def factory(db=None):
        return TestClient(
            create_app(settings=settings, llm=llm, store=MemoryVectorStore(), db=db or Database(":memory:"))
        )

    return factory


def drain_until(ws, predicate, limit=30):
    for _ in range(limit):
        ev = ws.receive_json()
        if predicate(ev):
            return ev
    raise AssertionError("expected event not received")


def is_msg(kind=None, author=None):
    def check(ev):
        m = ev.get("message") if ev["type"] == "message" else None
        return bool(m) and (kind is None or m["kind"] == kind) and (author is None or m["author"] == author)

    return check


def wait_for(client, predicate, timeout=3.0) -> bool:
    """Poll `predicate` on the server's event loop until true (or timeout)."""

    async def poll() -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            await asyncio.sleep(0.02)
        return False

    return client.portal.call(poll)


def connect(client, name):
    ws = client.websocket_connect(f"/ws?name={name}")
    return ws


def test_mention_gets_reply_and_plain_chat_does_not(llm, make_client):
    llm.script["intent"] = {"intent": "chitchat"}
    llm.chat_text = "hello from fake"
    with make_client() as client, connect(client, "alice") as ws:
        drain_until(ws, lambda e: e["type"] == "history")
        ws.send_json({"text": "just chatting"})
        drain_until(ws, is_msg("user", "alice"))
        client.portal.call(client.app.state.service.wait_idle)
        assert llm.chat_calls == []

        ws.send_json({"text": "@Aria hi"})
        ev = drain_until(ws, is_msg("aria"))
        assert ev["message"]["text"] == "hello from fake"


def test_llm_failure_posts_apology(llm, make_client):
    llm.fail = True
    with make_client() as client, connect(client, "alice") as ws:
        drain_until(ws, lambda e: e["type"] == "history")
        ws.send_json({"text": "@aria hello?"})
        ev = drain_until(ws, is_msg("aria"))
        assert "couldn't reach" in ev["message"]["text"]


def test_booking_flow_with_confirmation_card(llm, make_client):
    llm.script["intent"] = {"intent": "book_table"}
    llm.script["restaurant_name"] = {"restaurant_name": "Saffron Table", "party_size": 4, "time": "8pm"}
    with make_client() as client, connect(client, "alice") as ws:
        drain_until(ws, lambda e: e["type"] == "history")
        ws.send_json({"text": "@Aria book Saffron Table for 4 at 8pm"})
        card_msg = drain_until(ws, is_msg("aria"))["message"]
        card = card_msg["card"]
        assert card["status"] == "pending" and card["time"] == "20:00"
        assert client.get("/api/bookings").json() == []

        ws.send_json({"type": "confirm", "action_id": card["action_id"], "approve": True})
        update = drain_until(ws, lambda e: e["type"] == "message_update")
        assert update["message"]["card"]["status"] == "confirmed"
        assert update["message"]["card"]["decided_by"] == "alice"
        done = drain_until(ws, is_msg("aria"))
        assert done["message"]["text"].startswith("Booked!")

        bookings = client.get("/api/bookings").json()
        assert len(bookings) == 1 and bookings[0]["restaurant_name"] == "Saffron Table"

        # A second click on the same card is ignored (no double booking).
        ws.send_json({"type": "confirm", "action_id": card["action_id"], "approve": True})
        ws.send_json({"text": "marker"})  # handled after the duplicate confirm
        drain_until(ws, lambda e: e["type"] == "message" and e["message"]["text"] == "marker")
        client.portal.call(client.app.state.service.wait_idle)
        assert len(client.get("/api/bookings").json()) == 1


def test_cancel_booking_flow_with_confirmation_card(llm, make_client):
    db = Database(":memory:")
    llm.script["intent"] = {"intent": "cancel_booking"}
    llm.script["restaurant_name"] = {"restaurant_name": "Saffron Table"}
    with make_client(db) as client, connect(client, "alice") as ws:
        asyncio.run(db.add_booking(BookingRow(id="b-1", restaurant_id="r01", restaurant_name="Saffron Table",
                              party_size=4, time="20:00", booked_by="alice", ts="")))
        drain_until(ws, lambda e: e["type"] == "history")
        ws.send_json({"text": "@Aria cancel my table at Saffron Table"})
        card_msg = drain_until(ws, is_msg("aria"))["message"]
        card = card_msg["card"]
        assert card["type"] == "confirm_cancel" and card["status"] == "pending"
        assert card["restaurant"] == "Saffron Table" and card["party_size"] == 4
        assert len(client.portal.call(db.list_bookings)) == 1

        ws.send_json({"type": "confirm", "action_id": card["action_id"], "approve": True})
        update = drain_until(ws, lambda e: e["type"] == "message_update")
        assert update["message"]["card"]["status"] == "confirmed"
        done = drain_until(ws, is_msg("aria"))
        assert done["message"]["text"].startswith("Cancelled your booking at Saffron Table")
        assert client.portal.call(db.list_bookings) == []

        # A second click on the same card is ignored.
        ws.send_json({"type": "confirm", "action_id": card["action_id"], "approve": True})
        client.portal.call(client.app.state.service.wait_idle)
        assert client.portal.call(db.list_bookings) == []


def test_upload_then_ask_with_citations(llm, make_client):
    llm.script["intent"] = {"intent": "ask_docs"}
    llm.chat_text = "Leave is 24 days [1]."
    with make_client() as client, connect(client, "alice") as ws:
        drain_until(ws, lambda e: e["type"] == "history")
        r = client.post(
            "/api/upload",
            files={"file": ("handbook.md", b"Annual leave is 24 days per year.", "text/markdown")},
            data={"uploader": "alice"},
        )
        assert r.status_code == 200 and r.json()["name"] == "handbook.md"
        notice = drain_until(
            ws, lambda e: e["type"] == "message" and "uploaded" in e["message"]["text"]
        )
        assert "uploaded handbook.md" in notice["message"]["text"]
        assert [d["name"] for d in client.get("/api/documents").json()] == ["handbook.md"]

        ws.send_json({"text": "@Aria how many days of annual leave?"})
        ev = drain_until(ws, is_msg("aria"))
        assert ev["message"]["citations"][0]["source"] == "handbook.md"


def test_upload_validation(make_client):
    with make_client() as client:
        bad = client.post("/api/upload", files={"file": ("x.exe", b"MZ")}, data={"uploader": "a"})
        assert bad.status_code == 400
        too_big = client.post(
            "/api/upload",
            files={"file": ("big.txt", b"a" * (10 * 1024 * 1024 + 5))},
            data={"uploader": "a"},
        )
        assert too_big.status_code == 413


def test_proactive_suggestion_after_enough_chatter(llm, make_client):
    llm.script["speak"] = {"speak": True, "offer": "restaurants"}
    llm.script["cuisine"] = {"cuisine": "", "area": "", "max_price": 0, "party_size": 0}
    with make_client() as client, connect(client, "alice") as ws:
        drain_until(ws, lambda e: e["type"] == "history")
        ws.send_json({"text": "where should we eat tonight?"})
        ws.send_json({"text": "no idea, you?"})  # proactive_every_n = 2
        ev = drain_until(ws, is_msg("aria"))
        assert ev["message"]["text"].startswith("Sounds like you're deciding where to eat")


def test_proactive_silence_posts_nothing(llm, make_client):
    llm.script["speak"] = {"speak": False, "offer": "none"}
    with make_client() as client, connect(client, "alice") as ws:
        drain_until(ws, lambda e: e["type"] == "history")
        for text in ("haha", "lol", "nice", "ok"):
            ws.send_json({"text": text})
        assert wait_for(client, lambda: llm.structured_calls.count("speak") >= 2)
        client.portal.call(client.app.state.service.wait_idle)
        assert not any(m.kind == "aria" for m in client.app.state.room.history)


def test_history_survives_restart(llm, make_client):
    db = Database(":memory:")
    llm.script["intent"] = {"intent": "chitchat"}
    with make_client(db) as client, connect(client, "alice") as ws:
        drain_until(ws, lambda e: e["type"] == "history")
        ws.send_json({"text": "remember me"})
        drain_until(ws, is_msg("user", "alice"))
    with make_client(db) as client, connect(client, "bob") as ws:
        history = drain_until(ws, lambda e: e["type"] == "history")["messages"]
        assert any(m["text"] == "remember me" for m in history)
        assert not any(m["kind"] == "system" for m in history)  # join/leave notices not stored


def test_names_validated(make_client):
    with make_client() as client, connect(client, "alice") as ws:
        drain_until(ws, lambda e: e["type"] == "history")
        for bad in ("alice", "ALICE", "aria"):
            with pytest.raises(WebSocketDisconnect) as exc, connect(client, bad) as ws2:
                ws2.receive_json()
            assert exc.value.code == 4409
        with pytest.raises(WebSocketDisconnect) as exc, connect(client, "") as ws3:
            ws3.receive_json()
        assert exc.value.code == 4400


def tiny_png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (1, 1)).save(buffer, format="PNG")
    return buffer.getvalue()


class _CaptionBoomLLM(FakeLLM):
    """caption_image always fails with Ollama returning HTTP 500."""

    async def caption_image(self, image_bytes: bytes, mime_type: str) -> str:
        request = httpx.Request("POST", "http://ollama.test/api/chat")
        response = httpx.Response(500, request=request)
        raise httpx.HTTPStatusError(
            "Server error '500 Internal Server Error'", request=request, response=response
        )


class _CaptionOllamaVisionMissingLLM(FakeLLM):
    """caption_image fails with Ollama's real 'image input is not supported / mmproj' body."""

    async def caption_image(self, image_bytes: bytes, mime_type: str) -> str:
        request = httpx.Request("POST", "http://ollama.test/api/chat")
        inner = json.dumps(
            {
                "error": {
                    "code": 500,
                    "message": "image input is not supported - hint: if this is unexpected, you may need to provide the mmproj",
                    "type": "server_error",
                }
            }
        )
        response = httpx.Response(500, request=request, json={"error": inner})
        raise httpx.HTTPStatusError("Ollama error", request=request, response=response)


def test_upload_ollama_vision_missing_surfaces_hint(settings):
    with TestClient(
        create_app(
            settings=settings,
            llm=_CaptionOllamaVisionMissingLLM(),
            store=MemoryVectorStore(),
            db=Database(":memory:"),
        )
    ) as client:
        r = client.post(
            "/api/upload",
            files={"file": ("photo.png", tiny_png(), "image/png")},
            data={"uploader": "alice"},
        )
        assert r.status_code == 502
        detail = r.json()["detail"]
        assert "image input is not supported" in detail
        assert "mmproj" in detail
        assert "vision projectors may be missing" in detail


def test_upload_ollama_500_on_image_returns_clear_502(settings):
    with TestClient(
        create_app(
            settings=settings,
            llm=_CaptionBoomLLM(),
            store=MemoryVectorStore(),
            db=Database(":memory:"),
        )
    ) as client:
        r = client.post(
            "/api/upload",
            files={"file": ("photo.png", tiny_png(), "image/png")},
            data={"uploader": "alice"},
        )
        assert r.status_code == 502
        detail = r.json()["detail"]
        assert "Ollama" in detail and "500" in detail


def test_upload_unreadable_image_returns_400(settings):
    class _CaptionFailsLLM(FakeLLM):
        async def caption_image(self, image_bytes: bytes, mime_type: str) -> str:
            raise ValueError("could not read image: not a PNG")

    with TestClient(
        create_app(
            settings=settings,
            llm=_CaptionFailsLLM(),
            store=MemoryVectorStore(),
            db=Database(":memory:"),
        )
    ) as client:
        r = client.post(
            "/api/upload",
            files={"file": ("photo.png", b"not an image at all", "image/png")},
            data={"uploader": "alice"},
        )
        assert r.status_code == 400
        assert "could not read image" in r.json()["detail"].lower()


def test_health_and_presence(make_client):
    with make_client() as client:
        assert client.get("/health").json() == {"status": "ok"}
        with connect(client, "alice") as a:
            drain_until(a, lambda e: e["type"] == "history")
            with connect(client, "bob") as b:
                pres = drain_until(b, lambda e: e["type"] == "presence")
                assert set(pres["users"]) == {"alice", "bob", "Aria"}
