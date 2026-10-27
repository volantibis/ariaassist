"""The eval harness itself is tested with the fake LLM (scores are about the harness, not a model)."""

from eval.run import (
    DOCS_DIR,
    eval_retrieval,
    eval_router,
    eval_speak,
    load_cases,
    render_markdown,
    to_history,
)


def test_cases_file_is_well_formed():
    cases = load_cases()
    suites = {c["suite"] for c in cases}
    assert suites == {"router", "speak", "retrieval"}
    intents = {"ask_docs", "summarize", "find_restaurant", "book_table", "cancel_booking", "chitchat"}
    assert {c["expect"] for c in cases if c["suite"] == "router"} == intents
    assert {c["expect"] for c in cases if c["suite"] == "speak"} == {"restaurants", "summary", "none"}
    handbook = (DOCS_DIR / "company_handbook.md").read_text()
    for c in cases:
        if c["suite"] == "retrieval":
            assert c["expect_substring"].lower() in " ".join(handbook.lower().split())


def test_to_history_marks_aria_as_assistant():
    h = to_history(["alice: hi", "Aria: hello"])
    assert [m["role"] for m in h] == ["user", "assistant"]
    assert h[1]["name"] is None


async def test_router_scoring(llm):
    llm.script["intent"] = {"intent": "chitchat"}  # always answers chitchat
    cases = [c for c in load_cases() if c["suite"] == "router"]
    res = await eval_router(llm, cases)
    expected = sum(c["expect"] == "chitchat" for c in cases)
    assert res.total == len(cases) and res.correct == expected
    assert len(res.failures) == len(cases) - expected


async def test_speak_scoring(llm):
    llm.script["speak"] = {"speak": False, "offer": "none"}
    cases = [c for c in load_cases() if c["suite"] == "speak"]
    res = await eval_speak(llm, cases)
    assert res.correct == sum(c["expect"] == "none" for c in cases)


async def test_retrieval_scoring_with_hash_embedder(llm, store, settings):
    cases = [c for c in load_cases() if c["suite"] == "retrieval"]
    res = await eval_retrieval(llm, store, settings, cases)
    assert res.total == 8
    assert res.accuracy >= 0.75  # keyword-overlap embedder should find most answers
    assert 0.0 <= res.extra["hit@1"] <= res.accuracy


async def test_markdown_report(llm):
    llm.script["intent"] = {"intent": "chitchat"}
    res = await eval_router(llm, [c for c in load_cases() if c["suite"] == "router"][:3])
    md = render_markdown("ollama", "qwen3:4b", [res])
    assert "| router |" in md and "qwen3:4b" in md
