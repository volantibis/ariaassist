"""SQLite persistence for messages, documents and bookings.

Uses the stdlib `sqlite3` driver, with every call pushed onto a worker thread so the
event loop never blocks. One connection guarded by a lock is plenty for a demo.
Swap this class for Cosmos DB / Postgres later without touching callers.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    ts TEXT NOT NULL,
    author TEXT NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    extra TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    ts TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    chunks INTEGER NOT NULL,
    uploaded_by TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bookings (
    id TEXT PRIMARY KEY,
    ts TEXT NOT NULL,
    restaurant_id TEXT NOT NULL,
    restaurant_name TEXT NOT NULL,
    party_size INTEGER NOT NULL,
    time TEXT NOT NULL,
    booked_by TEXT NOT NULL
);
"""


@dataclass
class DocumentRow:
    id: str
    name: str
    kind: str  # "pdf" | "text" | "image"
    chunks: int
    uploaded_by: str
    ts: str


@dataclass
class BookingRow:
    id: str
    restaurant_id: str
    restaurant_name: str
    party_size: int
    time: str
    booked_by: str
    ts: str


def _now() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    def __init__(self, path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)

    # -- low level ---------------------------------------------------------
    def _run(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur.fetchall()

    async def _arun(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        return await asyncio.to_thread(self._run, sql, params)

    # -- messages ----------------------------------------------------------
    async def save_message(self, msg: dict[str, Any]) -> None:
        """Insert or replace a message. `msg` is the dict form of chat.room.Message."""
        extra = {k: msg[k] for k in ("card", "citations") if msg.get(k) is not None}
        await self._arun(
            "INSERT OR REPLACE INTO messages (id, ts, author, kind, text, extra) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (msg["id"], msg["ts"], msg["author"], msg["kind"], msg["text"], json.dumps(extra)),
        )

    async def recent_messages(self, limit: int = 200) -> list[dict[str, Any]]:
        """Return the newest `limit` messages, oldest first."""
        rows = await self._arun(
            "SELECT * FROM (SELECT rowid AS seq, * FROM messages "
            "ORDER BY ts DESC, rowid DESC LIMIT ?) ORDER BY ts ASC, seq ASC",
            (limit,),
        )
        out = []
        for r in rows:
            d = {k: r[k] for k in ("id", "ts", "author", "kind", "text")}
            d.update(json.loads(r["extra"]))
            out.append(d)
        return out

    # -- documents ---------------------------------------------------------
    async def add_document(self, doc: DocumentRow) -> None:
        await self._arun(
            "INSERT OR REPLACE INTO documents (id, ts, name, kind, chunks, uploaded_by) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (doc.id, doc.ts or _now(), doc.name, doc.kind, doc.chunks, doc.uploaded_by),
        )

    async def list_documents(self) -> list[DocumentRow]:
        rows = await self._arun("SELECT * FROM documents ORDER BY ts DESC")
        return [DocumentRow(**{k: r[k] for k in r.keys()}) for r in rows]

    # -- bookings ----------------------------------------------------------
    async def add_booking(self, b: BookingRow) -> None:
        await self._arun(
            "INSERT INTO bookings (id, ts, restaurant_id, restaurant_name, party_size, time, "
            "booked_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                b.id,
                b.ts or _now(),
                b.restaurant_id,
                b.restaurant_name,
                b.party_size,
                b.time,
                b.booked_by,
            ),
        )

    async def list_bookings(self) -> list[BookingRow]:
        rows = await self._arun("SELECT * FROM bookings ORDER BY ts DESC")
        return [BookingRow(**{k: r[k] for k in r.keys()}) for r in rows]

    async def delete_booking(self, booking_id: str) -> bool:
        """Delete the booking with the given id. Returns True if a row existed."""
        before = await self._arun("SELECT COUNT(*) AS n FROM bookings WHERE id = ?", (booking_id,))
        await self._arun("DELETE FROM bookings WHERE id = ?", (booking_id,))
        return before[0]["n"] > 0
