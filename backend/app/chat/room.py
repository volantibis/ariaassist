"""The chat room: connected users, message history, broadcasting, persistence."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import WebSocket

from app.db import Database

MAX_HISTORY = 200
ARIA_NAME = "Aria"


@dataclass
class Message:
    author: str
    text: str
    kind: Literal["user", "aria", "system"] = "user"
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    ts: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    card: dict[str, Any] | None = None  # e.g. a booking confirmation card
    citations: list[dict[str, Any]] | None = None  # RAG sources for an Aria answer


class Room:
    def __init__(self, db: Database) -> None:
        self._db = db
        self.history: list[Message] = []
        self._conns: dict[str, WebSocket] = {}

    async def load(self) -> None:
        """Restore recent history from SQLite (call once at startup)."""
        rows = await self._db.recent_messages(MAX_HISTORY)
        self.history = [Message(**row) for row in rows]

    # -- membership --------------------------------------------------------
    @property
    def users(self) -> list[str]:
        return sorted(self._conns)

    def name_taken(self, name: str) -> bool:
        lowered = name.lower()
        return lowered == ARIA_NAME.lower() or lowered in {n.lower() for n in self._conns}

    async def join(self, name: str, ws: WebSocket) -> None:
        self._conns[name] = ws
        await ws.send_json({"type": "history", "messages": [asdict(m) for m in self.history]})
        await self.add(Message(author="system", text=f"{name} joined", kind="system"))
        await self.broadcast_presence()

    async def leave(self, name: str) -> None:
        if self._conns.pop(name, None) is not None:
            await self.add(Message(author="system", text=f"{name} left", kind="system"))
            await self.broadcast_presence()

    # -- messages ----------------------------------------------------------
    async def add(self, msg: Message) -> None:
        """Append, persist (system notices are not persisted) and broadcast."""
        self.history.append(msg)
        del self.history[:-MAX_HISTORY]
        if msg.kind != "system":
            await self._db.save_message(asdict(msg))
        await self.broadcast({"type": "message", "message": asdict(msg)})

    async def update_card(self, message_id: str, patch: dict[str, Any]) -> None:
        """Merge `patch` into a message's card and tell every client."""
        for msg in self.history:
            if msg.id == message_id and msg.card is not None:
                msg.card = {**msg.card, **patch}
                await self._db.save_message(asdict(msg))
                await self.broadcast({"type": "message_update", "message": asdict(msg)})
                return

    # -- events ------------------------------------------------------------
    async def broadcast_presence(self) -> None:
        await self.broadcast({"type": "presence", "users": self.users + [ARIA_NAME]})

    async def set_typing(self, who: str, on: bool) -> None:
        await self.broadcast({"type": "typing", "who": who, "on": on})

    async def broadcast(self, event: dict[str, Any]) -> None:
        for name, ws in list(self._conns.items()):  # copy: connections may drop meanwhile
            try:
                await ws.send_json(event)
            except Exception:  # noqa: BLE001 - a dead socket must not break the others
                self._conns.pop(name, None)
