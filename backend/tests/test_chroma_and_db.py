import pytest

from app.db import Database
from app.providers.base import Chunk
from tests.conftest import hash_embed


async def test_db_messages_roundtrip_with_extras_and_ordering():
    db = Database(":memory:")
    base = {"author": "a", "kind": "user", "ts": "2026-01-01T00:00:0"}
    for i in range(5):
        await db.save_message({**base, "id": f"m{i}", "ts": f"2026-01-01T00:00:0{i}", "text": f"t{i}"})
    await db.save_message(
        {**base, "id": "card", "ts": "2026-01-01T00:00:09", "text": "c", "card": {"status": "pending"}}
    )
    await db.save_message(  # same id again = update (used for card status changes)
        {**base, "id": "card", "ts": "2026-01-01T00:00:09", "text": "c", "card": {"status": "confirmed"}}
    )
    rows = await db.recent_messages(limit=3)
    assert [r["id"] for r in rows] == ["m3", "m4", "card"]
    assert rows[-1]["card"] == {"status": "confirmed"}
    assert "citations" not in rows[0]


async def test_chroma_store_roundtrip(tmp_path):
    pytest.importorskip("chromadb")
    from app.providers.chroma_store import ChromaVectorStore

    store = ChromaVectorStore(str(tmp_path / "chroma"))
    assert await store.search(hash_embed("x"), k=2) == []  # before any index exists
    await store.ensure_index(64)
    chunks = [
        Chunk("1", "cats sleep all day", "pets.txt"),
        Chunk("2", "rockets burn fuel", "space.pdf", page=7),
    ]
    await store.upsert(chunks, [hash_embed(c.text) for c in chunks])
    hits = await store.search(hash_embed("rockets fuel"), k=2)
    assert hits[0].chunk.source == "space.pdf" and hits[0].chunk.page == 7
    assert hits[1].chunk.page is None
    assert hits[0].score > hits[1].score

    await store.delete_source("space.pdf")
    assert [h.chunk.source for h in await store.search(hash_embed("rockets"), k=5)] == ["pets.txt"]

    with pytest.raises(ValueError, match="dim 64"):
        await store.ensure_index(768)
    await store.ensure_index(768, recreate=True)
    assert await store.search([0.1] * 768, k=2) == []

    # persistence: a new client on the same path sees the same collection
    again = ChromaVectorStore(str(tmp_path / "chroma"))
    await again.ensure_index(768)
