"""State carried through the LangGraph agent. Plain dicts only, so it can be checkpointed."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypedDict

from app.config import Settings
from app.db import Database
from app.providers.base import LLMProvider, VectorStore


class AgentState(TypedDict, total=False):
    trigger: Literal["mention", "proactive"]
    requester: str  # who mentioned Aria ("" for proactive runs)
    history: list[dict]  # [{"role", "content", "name"}], oldest first, no system notices
    query: str  # the triggering message with "@Aria" removed
    intent: str  # router output
    offer: str  # proactive decision: restaurants | summary | none
    pending: dict  # booking awaiting human confirmation
    approved: bool
    confirmed_by: str
    reply: str  # text to post; "" means stay silent
    citations: list[dict]


@dataclass
class AgentDeps:
    """Everything a node may need. Injected, never imported, so tests can fake it."""

    llm: LLMProvider
    store: VectorStore
    db: Database
    settings: Settings
