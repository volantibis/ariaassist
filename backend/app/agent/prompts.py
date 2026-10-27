"""Prompts and JSON schemas for every agent step.

Small models fail on long, multi-goal prompts, so each step has its own short prompt and
a strict JSON schema. Unknown values are encoded as "" or 0 (not null) because small
models and grammar-constrained decoding handle those more reliably.
"""

from __future__ import annotations

from app.providers.base import ChatMessage

INTENTS = ["ask_docs", "summarize", "find_restaurant", "book_table", "cancel_booking", "chitchat"]

ARIA_SYSTEM = (
    "You are Aria, an AI member of a group chat. Reply helpfully in 1-3 short sentences. "
    "Messages are prefixed with the sender's name. Do not prefix your own reply with a name."
)

ROUTER_SYSTEM = """Classify the message addressed to Aria into exactly one intent.

Intents:
- ask_docs: needs facts from uploaded documents, PDFs or images (policies, specs, file contents)
- summarize: asks for a recap or summary of the conversation
- find_restaurant: wants restaurant ideas or suggestions of where to eat
- book_table: wants to reserve or book a table (including "yes, book it")
- cancel_booking: wants to cancel a table they previously booked (including "never mind that booking")
- chitchat: anything else (greetings, general questions, jokes)

Examples:
"what does the leave policy say about carry-over?" -> ask_docs
"what is in the screenshot I uploaded?" -> ask_docs
"can you recap what we discussed?" -> summarize
"tldr?" -> summarize
"any good sushi places nearby?" -> find_restaurant
"where should we eat tonight?" -> find_restaurant
"book Saffron Table for 4 at 8pm" -> book_table
"yes, book it" -> book_table
"cancel my table at Saffron Table" -> cancel_booking
"never mind the booking at Bella Notte" -> cancel_booking
"hi aria" -> chitchat
"tell me a joke" -> chitchat"""

ROUTE_SCHEMA = {
    "type": "object",
    "properties": {"intent": {"type": "string", "enum": INTENTS}},
    "required": ["intent"],
}

SPEAK_SYSTEM = """You are Aria, a quiet AI member of a group chat. Decide whether to speak
up WITHOUT being asked. Speak only if you can add clear value:
- offer "restaurants" if the group is deciding where or what to eat
- offer "summary" if the conversation became long or confusing
- otherwise do not speak
Small talk, jokes and chatter between people: do not speak."""

SPEAK_SCHEMA = {
    "type": "object",
    "properties": {
        "speak": {"type": "boolean"},
        "offer": {"type": "string", "enum": ["restaurants", "summary", "none"]},
    },
    "required": ["speak", "offer"],
}

RESTAURANT_SYSTEM = """Extract restaurant search filters from the conversation.
Use "" or 0 for anything not mentioned.
cuisine: e.g. italian, chinese, north indian. area: a neighbourhood name.
max_price: 1 (cheap), 2 (mid), 3 (expensive), or 0 if not mentioned. party_size: number of people or 0."""

RESTAURANT_SCHEMA = {
    "type": "object",
    "properties": {
        "cuisine": {"type": "string"},
        "area": {"type": "string"},
        "max_price": {"type": "integer"},
        "party_size": {"type": "integer"},
    },
    "required": ["cuisine", "area", "max_price", "party_size"],
}

BOOKING_SYSTEM = """Extract table booking details from the conversation.
Use the restaurant most recently discussed or named. Use "" or 0 when a detail is unknown.
restaurant_name: the name as written. party_size: number of people.
time: the time as written, like "8pm" or "20:30"."""

BOOKING_SCHEMA = {
    "type": "object",
    "properties": {
        "restaurant_name": {"type": "string"},
        "party_size": {"type": "integer"},
        "time": {"type": "string"},
    },
    "required": ["restaurant_name", "party_size", "time"],
}

CANCEL_SYSTEM = """Extract the restaurant name of a table booking the user wants to cancel.
Use "" when no restaurant is mentioned."""

CANCEL_SCHEMA = {
    "type": "object",
    "properties": {"restaurant_name": {"type": "string"}},
    "required": ["restaurant_name"],
}

SUMMARY_SYSTEM = (
    "Summarize the group conversation below for someone who just joined. Use at most 5 short "
    "bullet points. Mention decisions, open questions and who proposed what. Do not invent details."
)

DOCS_SYSTEM = (
    "Answer the question using ONLY the numbered context. Cite sources like [1] after the "
    "claims they support. If the answer is not in the context, say you could not find it in "
    "the uploaded files. Be concise."
)


def transcript(history: list[dict], n: int) -> str:
    """Render the last n messages as 'name: text' lines."""
    lines = [f"{m.get('name') or m['role']}: {m['content']}" for m in history[-n:]]
    return "\n".join(lines)


def to_chat_messages(history: list[dict], n: int = 12) -> list[ChatMessage]:
    """Convert stored history dicts into provider ChatMessages (with the system prompt)."""
    msgs = [ChatMessage(role="system", content=ARIA_SYSTEM)]
    for m in history[-n:]:
        msgs.append(ChatMessage(role=m["role"], content=m["content"], name=m.get("name")))
    return msgs
