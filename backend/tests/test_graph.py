"""Agent behaviour, tested node-by-node through the compiled LangGraph with a scripted LLM."""

from langgraph.types import Command

from app.db import BookingRow
from app.ingest.pipeline import ingest_file
from tests.conftest import chat_history, config, make_state


async def run(graph, state, thread="t1"):
    return await graph.ainvoke(state, config(thread))


# ---------------------------------------------------------------- routing
async def test_invalid_intent_falls_back_to_chitchat(graph, llm):
    llm.script["intent"] = {"intent": "make_coffee"}
    llm.chat_text = "hello there"
    out = await run(graph, make_state("hi"))
    assert out["intent"] == "chitchat" and out["reply"] == "hello there"


async def test_router_json_failure_falls_back_to_chitchat(graph, llm):
    llm.script["intent"] = ValueError("bad json")
    out = await run(graph, make_state("hi"))
    assert out["intent"] == "chitchat"


# -------------------------------------------------------------------- RAG
async def seed_doc(deps):
    await ingest_file(
        llm=deps.llm, store=deps.store, db=deps.db, settings=deps.settings,
        filename="handbook.md", uploaded_by="bob",
        data=b"Annual leave is 24 days per year. Unused leave carries over up to 5 days.",
    )


async def test_ask_docs_returns_only_cited_sources(graph, llm, deps):
    await seed_doc(deps)
    llm.script["intent"] = {"intent": "ask_docs"}
    llm.chat_text = "You get 24 days of annual leave [1]."
    out = await run(graph, make_state("how many days of annual leave do I get?"))
    assert out["reply"].startswith("You get 24 days")
    assert [c["source"] for c in out["citations"]] == ["handbook.md"]
    prompt = llm.chat_calls[-1][-1].content
    assert "Context:" in prompt and "[1] (handbook.md)" in prompt


async def test_ask_docs_shows_all_hits_when_model_forgets_to_cite(graph, llm, deps):
    await seed_doc(deps)
    llm.script["intent"] = {"intent": "ask_docs"}
    llm.chat_text = "24 days."
    out = await run(graph, make_state("annual leave days?"))
    assert len(out["citations"]) >= 1


async def test_ask_docs_with_nothing_relevant_does_not_call_the_llm(graph, llm):
    llm.script["intent"] = {"intent": "ask_docs"}
    out = await run(graph, make_state("what does the contract say?"))
    assert "couldn't find" in out["reply"]
    assert llm.chat_calls == []


# ------------------------------------------------------------- summarize
async def test_summarize(graph, llm):
    llm.script["intent"] = {"intent": "summarize"}
    llm.chat_text = "- they discussed dinner"
    history = chat_history("a: dinner?", "b: sure", "a: where?", "b: italian?")
    out = await run(graph, make_state("summarize", history=history))
    assert out["reply"] == "- they discussed dinner"


async def test_summarize_short_conversation(graph, llm):
    llm.script["intent"] = {"intent": "summarize"}
    out = await run(graph, make_state("summarize"))
    assert "isn't much" in out["reply"]
    assert llm.chat_calls == []


# ------------------------------------------------------------ restaurants
async def test_find_restaurant_applies_filters(graph, llm):
    llm.script["intent"] = {"intent": "find_restaurant"}
    llm.script["cuisine"] = {"cuisine": "Italian", "area": "", "max_price": 0, "party_size": 0}
    out = await run(graph, make_state("italian food?"))
    assert "Bella Notte" in out["reply"] and "Dragon Wok" not in out["reply"]
    assert "I'll book it" in out["reply"]


async def test_find_restaurant_relaxes_area_but_not_cuisine(graph, llm):
    llm.script["intent"] = {"intent": "find_restaurant"}
    llm.script["cuisine"] = {"cuisine": "thai", "area": "indiranagar", "max_price": 0, "party_size": 0}
    out = await run(graph, make_state("thai in indiranagar"))
    assert "Thai Orchid" in out["reply"]  # no Thai in Indiranagar, area filter dropped


async def test_find_restaurant_unknown_cuisine(graph, llm):
    llm.script["intent"] = {"intent": "find_restaurant"}
    llm.script["cuisine"] = {"cuisine": "klingon", "area": "", "max_price": 0, "party_size": 0}
    out = await run(graph, make_state("klingon food"))
    assert "couldn't find" in out["reply"]


# ---------------------------------------------------------------- booking
def book_slots(llm, name="Saffron Table", party=4, time="8pm"):
    llm.script["intent"] = {"intent": "book_table"}
    llm.script["restaurant_name"] = {"restaurant_name": name, "party_size": party, "time": time}


async def test_booking_pauses_for_confirmation_then_executes(graph, llm, db):
    book_slots(llm)
    paused = await run(graph, make_state("book Saffron Table for 4 at 8pm"), "b1")
    payload = paused["__interrupt__"][0].value
    assert payload["restaurant_name"] == "Saffron Table"
    assert (payload["party_size"], payload["time"]) == (4, "20:00")
    assert await db.list_bookings() == []  # nothing written before approval

    done = await graph.ainvoke(Command(resume={"approve": True, "by": "alice"}), config("b1"))
    assert done["reply"].startswith("Booked! Saffron Table for 4 at 20:00")
    bookings = await db.list_bookings()
    assert len(bookings) == 1 and bookings[0].booked_by == "alice"


async def test_booking_declined_writes_nothing(graph, llm, db):
    book_slots(llm)
    await run(graph, make_state("book it"), "b2")
    done = await graph.ainvoke(Command(resume={"approve": False, "by": "alice"}), config("b2"))
    assert "cancelled" in done["reply"]
    assert await db.list_bookings() == []


async def test_booking_asks_for_missing_details(graph, llm):
    book_slots(llm, name="", party=0, time="")
    out = await run(graph, make_state("book a table"))
    assert "__interrupt__" not in out
    assert "which restaurant" in out["reply"] and "how many people" in out["reply"]
    assert "what time" in out["reply"]


async def test_booking_unknown_restaurant(graph, llm):
    book_slots(llm, name="Zzzz Qqqq Palace")
    out = await run(graph, make_state("book zzzz"))
    assert "couldn't find a restaurant" in out["reply"]


async def test_booking_validation_errors_are_explained_before_confirming(graph, llm):
    book_slots(llm, name="Tokyo Counter", party=8, time="8pm")
    out = await run(graph, make_state("book tokyo for 8"), "v1")
    assert "at most 4" in out["reply"] and "__interrupt__" not in out
    book_slots(llm, name="Tokyo Counter", party=2, time="5pm")
    out = await run(graph, make_state("book tokyo at 5pm"), "v2")
    assert "open 18:00" in out["reply"]


# ---------------------------------------------------------------- cancelling
async def seed_booking(db, booking_id="b-1", restaurant_id="r01", restaurant_name="Saffron Table",
                 party_size=4, time="20:00", booked_by="alice"):
    await db.add_booking(BookingRow(
        id=booking_id, restaurant_id=restaurant_id, restaurant_name=restaurant_name,
        party_size=party_size, time=time, booked_by=booked_by, ts="",
    ))


async def test_cancel_booking_pauses_then_deletes(graph, llm, db):
    await seed_booking(db)
    llm.script["intent"] = {"intent": "cancel_booking"}
    llm.script["restaurant_name"] = {"restaurant_name": "Saffron Table"}
    paused = await run(graph, make_state("cancel my table at Saffron Table", requester="alice"), "c1")
    payload = paused["__interrupt__"][0].value
    assert payload["type"] == "confirm_cancel"
    assert payload["restaurant_name"] == "Saffron Table"
    assert (payload["party_size"], payload["time"]) == (4, "20:00")
    assert len(await db.list_bookings()) == 1  # nothing deleted before approval

    done = await graph.ainvoke(Command(resume={"approve": True, "by": "alice"}), config("c1"))
    assert "Cancelled your booking at Saffron Table" in done["reply"]
    assert await db.list_bookings() == []


async def test_cancel_booking_declined_keeps_booking(graph, llm, db):
    await seed_booking(db)
    llm.script["intent"] = {"intent": "cancel_booking"}
    llm.script["restaurant_name"] = {"restaurant_name": "Saffron Table"}
    await run(graph, make_state("cancel my table at Saffron Table", requester="alice"), "c2")
    done = await graph.ainvoke(Command(resume={"approve": False, "by": "alice"}), config("c2"))
    assert "stays as it was" in done["reply"]
    assert len(await db.list_bookings()) == 1


async def test_cancel_booking_unknown_restaurant(graph, llm):
    llm.script["intent"] = {"intent": "cancel_booking"}
    llm.script["restaurant_name"] = {"restaurant_name": "Zzzz Qqqq Palace"}
    out = await run(graph, make_state("cancel at zzzz", requester="alice"))
    assert "__interrupt__" not in out
    assert "couldn't find a restaurant" in out["reply"]


async def test_cancel_booking_no_booking_found(graph, llm, db):
    await seed_booking(db)
    llm.script["intent"] = {"intent": "cancel_booking"}
    llm.script["restaurant_name"] = {"restaurant_name": "Bella Notte"}
    out = await run(graph, make_state("cancel my Bella Notte table", requester="alice"))
    assert "__interrupt__" not in out
    assert "don't see a booking" in out["reply"]


# -------------------------------------------------------------- proactive
async def test_proactive_stays_silent_by_default(graph, llm):
    llm.script["speak"] = {"speak": False, "offer": "none"}
    out = await run(graph, make_state("lol", trigger="proactive"))
    assert out["reply"] == ""


async def test_proactive_speak_false_overrides_offer(graph, llm):
    llm.script["speak"] = {"speak": False, "offer": "restaurants"}
    out = await run(graph, make_state("lol", trigger="proactive"))
    assert out["reply"] == ""


async def test_proactive_restaurant_offer_reuses_search(graph, llm):
    llm.script["speak"] = {"speak": True, "offer": "restaurants"}
    llm.script["cuisine"] = {"cuisine": "", "area": "", "max_price": 0, "party_size": 0}
    history = chat_history("a: where should we eat?", "b: no idea")
    out = await run(graph, make_state("no idea", trigger="proactive", history=history))
    assert out["reply"].startswith("Sounds like you're deciding where to eat")


async def test_proactive_summary_offer(graph, llm):
    llm.script["speak"] = {"speak": True, "offer": "summary"}
    out = await run(graph, make_state("x", trigger="proactive"))
    assert "@Aria summarize" in out["reply"]
