"""Glue between the chat room and the LangGraph agent.

Decides WHEN Aria acts (mention, or proactive after enough chatter), runs the graph,
posts the outcome, and handles Approve/Cancel clicks that resume a paused graph.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Callable, Coroutine
from typing import Any

from langgraph.types import Command

from app.agent.state import AgentState
from app.chat.mentions import is_mention, strip_mention
from app.chat.room import ARIA_NAME, Message, Room
from app.config import Settings

log = logging.getLogger("aria.service")
HISTORY_WINDOW = 30
APOLOGY = "Sorry, I couldn't reach my language model just now."


class AriaService:
    def __init__(
        self,
        *,
        graph: Any,
        room: Room,
        settings: Settings,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._graph = graph
        self._room = room
        self._settings = settings
        self._clock = clock
        # One model call at a time: an 8 GB machine cannot serve parallel generations.
        self.model_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()
        self._pending: dict[str, str] = {}  # action_id -> id of the card message
        self._since_last_check = 0
        self._last_proactive = -1e9

    # ------------------------------------------------------------ scheduling
    def spawn(self, coro: Coroutine[Any, Any, None]) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def wait_idle(self) -> None:
        """Wait for in-flight work (used by tests)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    # ---------------------------------------------------------------- inputs
    async def on_human_message(self, msg: Message) -> None:
        """Called after every human message has been added to the room."""
        if is_mention(msg.text):
            self._since_last_check = 0
            self.spawn(self._run("mention", msg))
            return
        self._since_last_check += 1
        s = self._settings
        due = (
            s.proactive_enabled
            and self._since_last_check >= s.proactive_every_n
            and self._clock() - self._last_proactive >= s.proactive_cooldown_s
            and not self._pending  # don't chatter while a confirmation is open
        )
        if due:
            self._since_last_check = 0
            self._last_proactive = self._clock()
            self.spawn(self._run("proactive", msg))

    async def handle_confirm(self, action_id: str, approve: bool, user: str) -> None:
        card_id = self._pending.pop(action_id, None)
        if card_id is None:  # already decided, or unknown
            return
        async with self.model_lock:
            try:
                result = await self._graph.ainvoke(
                    Command(resume={"approve": approve, "by": user}), self._config(action_id)
                )
            except Exception:  # noqa: BLE001
                log.exception("resume failed")
                await self._room.add(Message(author=ARIA_NAME, text=APOLOGY, kind="aria"))
                return
        status = "confirmed" if approve else "cancelled"
        await self._room.update_card(card_id, {"status": status, "decided_by": user})
        await self._post(result)

    # ------------------------------------------------------------- internals
    @staticmethod
    def _config(thread_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": thread_id}}

    def _history(self) -> list[dict]:
        msgs = [m for m in self._room.history if m.kind != "system"][-HISTORY_WINDOW:]
        return [
            {
                "role": "assistant" if m.kind == "aria" else "user",
                "content": m.text,
                "name": None if m.kind == "aria" else m.author,
            }
            for m in msgs
        ]

    async def _run(self, trigger: str, msg: Message) -> None:
        state: AgentState = {
            "trigger": trigger,  # type: ignore[typeddict-item]
            "requester": msg.author if trigger == "mention" else "",
            "history": self._history(),
            "query": strip_mention(msg.text) if trigger == "mention" else msg.text,
        }
        thread_id = uuid.uuid4().hex
        async with self.model_lock:
            if trigger == "mention":
                await self._room.set_typing(ARIA_NAME, True)
            try:
                result = await self._graph.ainvoke(state, self._config(thread_id))
            except Exception:  # noqa: BLE001 - model offline, OOM, timeout...
                log.exception("agent run failed (%s)", trigger)
                if trigger == "mention":
                    await self._room.add(Message(author=ARIA_NAME, text=APOLOGY, kind="aria"))
                return
            finally:
                if trigger == "mention":
                    await self._room.set_typing(ARIA_NAME, False)
        await self._post(result, thread_id=thread_id)

    async def _post(self, result: dict[str, Any], *, thread_id: str | None = None) -> None:
        interrupts = result.get("__interrupt__")
        if interrupts and thread_id:
            p = interrupts[0].value
            card_type = p.get("type", "confirm_booking")
            card = {
                "type": card_type,
                "action_id": thread_id,
                "restaurant": p["restaurant_name"],
                "party_size": p["party_size"],
                "time": p["time"],
                "requested_by": p.get("requested_by", ""),
                "status": "pending",
            }
            if card_type == "confirm_cancel":
                text = (
                    f"Cancel your booking at {p['restaurant_name']} for {p['party_size']} "
                    f"at {p['time']}. Shall I go ahead?"
                )
            else:
                text = (
                    f"I can book {p['restaurant_name']} for {p['party_size']} at {p['time']}. "
                    f"Shall I go ahead?"
                )
            msg = Message(
                author=ARIA_NAME,
                kind="aria",
                text=text,
                card=card,
            )
            self._pending[thread_id] = msg.id
            await self._room.add(msg)
            return
        reply = (result.get("reply") or "").strip()
        if not reply:  # proactive run decided to stay silent
            return
        await self._room.add(
            Message(
                author=ARIA_NAME,
                kind="aria",
                text=reply,
                citations=result.get("citations") or None,
            )
        )
