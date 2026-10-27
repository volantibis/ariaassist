"""The Aria agent as a LangGraph state machine.

    START -> route ----> ask_docs | summarize | find_restaurant | chitchat -> END
       |         \\-> extract_slots -> confirm (human approves) -> execute | cancel -> END
       \\-> decide_speak (proactive runs) -> find_restaurant | offer_summary | END

Each node is a small async function that reads AgentState and returns a partial update.
`confirm` calls LangGraph's `interrupt()`: the graph pauses, the chat shows an Approve /
Cancel card, and the run resumes with `Command(resume=...)` when someone clicks.
NOTE: on resume a node re-runs from its first line, so `confirm` does nothing but ask.
"""

from __future__ import annotations

import re
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agent import prompts
from app.agent.state import AgentDeps, AgentState
from app.providers.base import ChatMessage, LLMProvider
from app.tools.bookings import (
    BookingError,
    book_table,
    cancel_booking,
    find_booking,
    validate_booking,
)
from app.tools.restaurants import (
    find_by_name,
    get_by_id,
    parse_time,
    search_restaurants,
)

CITATION_RE = re.compile(r"\[(\d+)\]")


async def classify_intent(llm: LLMProvider, history: list[dict], query: str) -> str:
    """Router step. Falls back to 'chitchat' if the model cannot produce a valid intent."""
    msgs = [
        ChatMessage("system", prompts.ROUTER_SYSTEM),
        ChatMessage(
            "user",
            f"Conversation:\n{prompts.transcript(history, 6)}\n\nMessage addressed to Aria: {query}",
        ),
    ]
    try:
        intent = (await llm.structured(msgs, prompts.ROUTE_SCHEMA)).get("intent")
    except ValueError:
        return "chitchat"
    return intent if intent in prompts.INTENTS else "chitchat"


async def decide_offer(llm: LLMProvider, history: list[dict]) -> str:
    """Proactive step. Returns 'restaurants', 'summary' or 'none'."""
    msgs = [
        ChatMessage("system", prompts.SPEAK_SYSTEM),
        ChatMessage("user", prompts.transcript(history, 8)),
    ]
    try:
        out = await llm.structured(msgs, prompts.SPEAK_SCHEMA)
    except ValueError:
        return "none"
    offer = out.get("offer", "none") if out.get("speak") else "none"
    return offer if offer in ("restaurants", "summary") else "none"


def build_graph(deps: AgentDeps):
    """Compile the agent. Returns a LangGraph app; invoke with a thread_id in the config."""
    llm, store, db, settings = deps.llm, deps.store, deps.db, deps.settings

    # ---------------------------------------------------------------- routing
    async def route(state: AgentState) -> dict[str, Any]:
        return {"intent": await classify_intent(llm, state["history"], state["query"])}

    def after_route(state: AgentState) -> str:
        return {
            "book_table": "extract_slots",
            "cancel_booking": "extract_cancel_slots",
        }.get(state["intent"], state["intent"])

    def entry(state: AgentState) -> str:
        return "decide_speak" if state["trigger"] == "proactive" else "route"

    # ------------------------------------------------------------------- RAG
    async def ask_docs(state: AgentState) -> dict[str, Any]:
        query = state["query"]
        [vector] = await llm.embed([query], is_query=True)
        hits = [
            h
            for h in await store.search(vector, k=settings.retrieval_k)
            if h.score >= settings.retrieval_min_score
        ]
        if not hits:
            return {"reply": "I couldn't find anything about that in the uploaded files."}
        context = "\n\n".join(
            f"[{i}] ({h.chunk.source}{f', p.{h.chunk.page}' if h.chunk.page else ''}) {h.chunk.text}"
            for i, h in enumerate(hits, start=1)
        )
        result = await llm.chat(
            [
                ChatMessage("system", prompts.DOCS_SYSTEM),
                ChatMessage("user", f"Context:\n{context}\n\nQuestion: {query}"),
            ]
        )
        cited = {int(n) for n in CITATION_RE.findall(result.text)}
        citations = [
            {
                "n": i,
                "source": h.chunk.source,
                "page": h.chunk.page,
                "kind": h.chunk.kind,
                "snippet": h.chunk.text[:160],
            }
            for i, h in enumerate(hits, start=1)
            if not cited or i in cited  # model forgot to cite: show everything retrieved
        ]
        return {"reply": result.text, "citations": citations}

    # ------------------------------------------------------------- summarize
    async def summarize(state: AgentState) -> dict[str, Any]:
        recent = state["history"][-40:]
        if len(recent) < 3:
            return {"reply": "There isn't much to summarize yet."}
        result = await llm.chat(
            [
                ChatMessage("system", prompts.SUMMARY_SYSTEM),
                ChatMessage("user", prompts.transcript(recent, 40)),
            ]
        )
        return {"reply": result.text}

    # ------------------------------------------------------------- chit-chat
    async def chitchat(state: AgentState) -> dict[str, Any]:
        result = await llm.chat(prompts.to_chat_messages(state["history"]))
        return {"reply": result.text or "I don't have anything to add to that."}

    # ----------------------------------------------------------- restaurants
    async def find_restaurant(state: AgentState) -> dict[str, Any]:
        msgs = [
            ChatMessage("system", prompts.RESTAURANT_SYSTEM),
            ChatMessage("user", prompts.transcript(state["history"], 8)),
        ]
        try:
            f = await llm.structured(msgs, prompts.RESTAURANT_SCHEMA)
        except ValueError:
            f = {}
        base = {
            "cuisine": (f.get("cuisine") or "").strip().lower() or None,
            "area": (f.get("area") or "").strip().lower() or None,
            "max_price": f.get("max_price") or None,
            "party_size": f.get("party_size") or None,
        }
        # Relax filters progressively so we rarely answer with nothing.
        results = []
        for drop in ((), ("area",), ("area", "max_price")):
            results = search_restaurants(**{k: v for k, v in base.items() if k not in drop})
            if results:
                break
        if not results:
            return {"reply": "I couldn't find a matching restaurant."}
        lines = [f"{i}. {r.describe()}" for i, r in enumerate(results, start=1)]
        intro = (
            "Sounds like you're deciding where to eat. Here are some ideas:"
            if state["trigger"] == "proactive"
            else "Here are some options:"
        )
        outro = "Tell me which one, how many people and what time, and I'll book it."
        return {"reply": "\n".join([intro, *lines, outro])}

    # ------------------------------------------------------------ proactive
    async def decide_speak(state: AgentState) -> dict[str, Any]:
        return {"offer": await decide_offer(llm, state["history"]), "reply": ""}

    def after_decide(state: AgentState) -> str:
        return {"restaurants": "find_restaurant", "summary": "offer_summary"}.get(
            state["offer"], END
        )

    async def offer_summary(state: AgentState) -> dict[str, Any]:
        return {"reply": "Lots going on! Want a recap? Just say '@Aria summarize'."}

    # -------------------------------------------------------------- booking
    async def extract_slots(state: AgentState) -> dict[str, Any]:
        msgs = [
            ChatMessage("system", prompts.BOOKING_SYSTEM),
            ChatMessage("user", prompts.transcript(state["history"], 15)),
        ]
        try:
            slots = await llm.structured(msgs, prompts.BOOKING_SCHEMA)
        except ValueError:
            return {"reply": "Sorry, I couldn't work out the booking details. Could you repeat them?"}

        name = (slots.get("restaurant_name") or "").strip()
        party = int(slots.get("party_size") or 0)
        raw_time = (slots.get("time") or "").strip()

        restaurant = find_by_name(name) if name else None
        if name and restaurant is None:
            return {"reply": f"I couldn't find a restaurant called '{name}'."}
        hhmm = parse_time(raw_time) if raw_time else None

        missing = []
        if restaurant is None:
            missing.append("which restaurant")
        if party < 1:
            missing.append("how many people")
        if hhmm is None:
            missing.append("what time")
        if missing:
            return {"reply": "To book a table I still need: " + ", ".join(missing) + "."}

        assert restaurant is not None and hhmm is not None
        try:
            validate_booking(restaurant, party, hhmm)
        except BookingError as e:
            return {"reply": str(e)}
        return {
            "pending": {
                "restaurant_id": restaurant.id,
                "restaurant_name": restaurant.name,
                "party_size": party,
                "time": hhmm,
                "requested_by": state.get("requester", ""),
            }
        }

    def after_slots(state: AgentState) -> str:
        return "confirm" if state.get("pending") else END

    def confirm(state: AgentState) -> dict[str, Any]:
        # Pauses the graph. The value passed to Command(resume=...) is returned here.
        decision = interrupt({"type": "confirm_booking", **state["pending"]})
        return {
            "approved": bool(decision.get("approve")),
            "confirmed_by": decision.get("by", ""),
        }

    def after_confirm(state: AgentState) -> str:
        return "execute" if state.get("approved") else "cancel"

    async def execute(state: AgentState) -> dict[str, Any]:
        p = state["pending"]
        restaurant = get_by_id(p["restaurant_id"])
        if restaurant is None:
            return {"reply": "Sorry, that restaurant is no longer available."}
        try:
            booking = await book_table(
                db,
                restaurant=restaurant,
                party_size=p["party_size"],
                hhmm=p["time"],
                booked_by=state.get("confirmed_by") or p.get("requested_by", ""),
            )
        except BookingError as e:
            return {"reply": str(e)}
        return {
            "reply": (
                f"Booked! {booking.restaurant_name} for {booking.party_size} at {booking.time}. "
                f"Reference {booking.id}. (This is a demo booking.)"
            )
        }

    async def cancel(state: AgentState) -> dict[str, Any]:
        return {"reply": "No problem, I've cancelled that booking request."}

    # ----------------------------------------------------------- cancelling
    async def extract_cancel_slots(state: AgentState) -> dict[str, Any]:
        msgs = [
            ChatMessage("system", prompts.CANCEL_SYSTEM),
            ChatMessage("user", prompts.transcript(state["history"], 15)),
        ]
        try:
            slots = await llm.structured(msgs, prompts.CANCEL_SCHEMA)
        except ValueError:
            return {"reply": "Sorry, I couldn't work out which booking to cancel."}

        name = (slots.get("restaurant_name") or "").strip()
        requester = state.get("requester", "")
        if not name:
            return {"reply": "Which restaurant should I cancel the booking at?"}
        restaurant = find_by_name(name)
        if restaurant is None:
            return {"reply": f"I couldn't find a restaurant called '{name}'."}
        booking = await find_booking(db, restaurant_id=restaurant.id, booked_by=requester)  # type: ignore[arg-type]
        if booking is None:
            return {"reply": f"I don't see a booking at {restaurant.name} under your name."}
        return {
            "pending": {
                "booking_id": booking.id,
                "restaurant_name": booking.restaurant_name,
                "party_size": booking.party_size,
                "time": booking.time,
                "requested_by": requester,
            }
        }

    def after_cancel_slots(state: AgentState) -> str:
        return "confirm_cancel" if state.get("pending") else END

    def confirm_cancel(state: AgentState) -> dict[str, Any]:
        # Pauses the graph; the resume value is returned by interrupt().
        decision = interrupt({"type": "confirm_cancel", **state["pending"]})
        return {
            "approved": bool(decision.get("approve")),
            "confirmed_by": decision.get("by", ""),
        }

    def after_confirm_cancel(state: AgentState) -> str:
        return "cancel_execute" if state.get("approved") else "cancel_decline"

    async def cancel_execute(state: AgentState) -> dict[str, Any]:
        p = state["pending"]
        try:
            row = await cancel_booking(
                db,
                booking_id=p["booking_id"],
                cancelled_by=state.get("confirmed_by") or p.get("requested_by", ""),
            )
        except BookingError as e:
            return {"reply": str(e)}
        return {
            "reply": (
                f"Cancelled your booking at {row.restaurant_name} for {row.party_size} at {row.time}. "
                f"(Reference {row.id}.)"
            )
        }

    async def cancel_decline(state: AgentState) -> dict[str, Any]:
        return {"reply": "No problem, your booking stays as it was."}

    # ----------------------------------------------------------------- wiring
    g = StateGraph(AgentState)
    for name, fn in [
        ("route", route),
        ("ask_docs", ask_docs),
        ("summarize", summarize),
        ("chitchat", chitchat),
        ("find_restaurant", find_restaurant),
        ("decide_speak", decide_speak),
        ("offer_summary", offer_summary),
        ("extract_slots", extract_slots),
        ("extract_cancel_slots", extract_cancel_slots),
        ("confirm", confirm),
        ("confirm_cancel", confirm_cancel),
        ("execute", execute),
        ("cancel_execute", cancel_execute),
        ("cancel", cancel),
        ("cancel_decline", cancel_decline),
    ]:
        g.add_node(name, fn)

    g.add_conditional_edges(START, entry, {"route": "route", "decide_speak": "decide_speak"})
    g.add_conditional_edges(
        "route",
        after_route,
        {
            "ask_docs": "ask_docs",
            "summarize": "summarize",
            "find_restaurant": "find_restaurant",
            "chitchat": "chitchat",
            "extract_slots": "extract_slots",
            "extract_cancel_slots": "extract_cancel_slots",
        },
    )
    g.add_conditional_edges(
        "decide_speak",
        after_decide,
        {"find_restaurant": "find_restaurant", "offer_summary": "offer_summary", END: END},
    )
    g.add_conditional_edges("extract_slots", after_slots, {"confirm": "confirm", END: END})
    g.add_conditional_edges("extract_cancel_slots", after_cancel_slots, {"confirm_cancel": "confirm_cancel", END: END})
    g.add_conditional_edges("confirm", after_confirm, {"execute": "execute", "cancel": "cancel"})
    g.add_conditional_edges(
        "confirm_cancel",
        after_confirm_cancel,
        {"cancel_execute": "cancel_execute", "cancel_decline": "cancel_decline"},
    )
    for terminal in (
        "ask_docs",
        "summarize",
        "chitchat",
        "find_restaurant",
        "offer_summary",
        "execute",
        "cancel",
        "cancel_execute",
        "cancel_decline",
    ):
        g.add_edge(terminal, END)

    # MemorySaver keeps paused (awaiting-confirmation) runs in process memory.
    return g.compile(checkpointer=MemorySaver())
