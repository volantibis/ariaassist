"""Evaluate the agent's decisions and retrieval against a model.

    cd backend
    python -m eval.run --provider ollama
    python -m eval.run --provider azure_foundry --write ../docs/EVAL.md

Suites (see eval/cases.jsonl):
  router     intent classification accuracy      (does Aria pick the right skill?)
  speak      proactive decision accuracy         (does she speak up only when useful?)
  retrieval  hit@k and hit@1 over the sample handbook (is the right chunk retrieved?)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from app.agent.graph import classify_intent, decide_offer
from app.config import Settings
from app.db import Database
from app.ingest.pipeline import ingest_file
from app.providers.base import LLMProvider, VectorStore
from app.providers.factory import make_llm, make_secrets
from app.providers.memory_store import MemoryVectorStore

HERE = Path(__file__).parent
CASES_FILE = HERE / "cases.jsonl"
DOCS_DIR = HERE / "docs"


@dataclass
class SuiteResult:
    name: str
    total: int = 0
    correct: int = 0
    failures: list[str] = field(default_factory=list)
    seconds: float = 0.0
    extra: dict[str, float] = field(default_factory=dict)

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0

    @property
    def ms_per_case(self) -> float:
        return 1000 * self.seconds / self.total if self.total else 0.0


def load_cases(path: Path = CASES_FILE) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def to_history(lines: list[str]) -> list[dict]:
    """'alice: hi' / 'Aria: hello' -> history dicts (Aria lines become assistant turns)."""
    out = []
    for line in lines:
        name, text = line.split(": ", 1)
        is_aria = name.lower() == "aria"
        out.append(
            {"role": "assistant" if is_aria else "user", "content": text, "name": None if is_aria else name}
        )
    return out


async def eval_router(llm: LLMProvider, cases: list[dict]) -> SuiteResult:
    res = SuiteResult("router")
    start = time.perf_counter()
    for c in cases:
        history = to_history(c.get("history", [])) + [{"role": "user", "content": c["text"], "name": "alice"}]
        got = await classify_intent(llm, history, c["text"])
        res.total += 1
        if got == c["expect"]:
            res.correct += 1
        else:
            res.failures.append(f'"{c["text"]}": expected {c["expect"]}, got {got}')
    res.seconds = time.perf_counter() - start
    return res


async def eval_speak(llm: LLMProvider, cases: list[dict]) -> SuiteResult:
    res = SuiteResult("speak")
    start = time.perf_counter()
    for c in cases:
        got = await decide_offer(llm, to_history(c["history"]))
        res.total += 1
        if got == c["expect"]:
            res.correct += 1
        else:
            res.failures.append(f'"{c["history"][-1]}": expected {c["expect"]}, got {got}')
    res.seconds = time.perf_counter() - start
    return res


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


async def eval_retrieval(
    llm: LLMProvider, store: VectorStore, settings: Settings, cases: list[dict], docs_dir: Path = DOCS_DIR
) -> SuiteResult:
    db = Database(":memory:")
    for path in sorted(docs_dir.glob("*.md")):
        await ingest_file(
            llm=llm, store=store, db=db, settings=settings,
            filename=path.name, data=path.read_bytes(), uploaded_by="eval",
        )
    res = SuiteResult("retrieval")
    top1 = 0
    start = time.perf_counter()
    for c in cases:
        [vec] = await llm.embed([c["question"]], is_query=True)
        hits = await store.search(vec, k=settings.retrieval_k)
        needle = _norm(c["expect_substring"])
        res.total += 1
        if any(needle in _norm(h.chunk.text) for h in hits):
            res.correct += 1
        else:
            res.failures.append(f'"{c["question"]}": "{c["expect_substring"]}" not in top {settings.retrieval_k}')
        if hits and needle in _norm(hits[0].chunk.text):
            top1 += 1
    res.seconds = time.perf_counter() - start
    res.extra["hit@1"] = top1 / res.total if res.total else 0.0
    return res


def render_markdown(provider: str, model: str, results: list[SuiteResult]) -> str:
    lines = [
        f"## {provider} ({model}), {date.today().isoformat()}",
        "",
        "| Suite | Score | Cases | ms/case | Notes |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        notes = ", ".join(f"{k} {v:.0%}" for k, v in r.extra.items())
        lines.append(f"| {r.name} | {r.accuracy:.0%} | {r.correct}/{r.total} | {r.ms_per_case:.0f} | {notes} |")
    failures = [(r.name, f) for r in results for f in r.failures]
    if failures:
        lines += ["", "<details><summary>Failures</summary>", ""]
        lines += [f"- **{name}** {f}" for name, f in failures]
        lines += ["", "</details>"]
    return "\n".join(lines) + "\n"


async def main_async(provider: str, suites: list[str], write: Path | None) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(
            llm_provider=provider, vector_provider="memory", sqlite_path=":memory:", upload_dir=tmp
        )
        llm = make_llm(settings, make_secrets(settings))
        store = MemoryVectorStore()
        cases = load_cases()
        by_suite = {s: [c for c in cases if c["suite"] == s] for s in ("router", "speak", "retrieval")}
        results: list[SuiteResult] = []
        try:
            if "router" in suites:
                results.append(await eval_router(llm, by_suite["router"]))
            if "speak" in suites:
                results.append(await eval_speak(llm, by_suite["speak"]))
            if "retrieval" in suites:
                results.append(await eval_retrieval(llm, store, settings, by_suite["retrieval"]))
        finally:
            close = getattr(llm, "aclose", None)
            if close:
                await close()
    model = settings.ollama_chat_model if provider == "ollama" else settings.azure_foundry_chat_deployment
    report = render_markdown(provider, model, results)
    print(report)
    if write:
        existing = write.read_text(encoding="utf-8") if write.exists() else "# Evaluation results\n\n"
        write.write_text(existing.rstrip() + "\n\n" + report, encoding="utf-8")
        print(f"appended to {write}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", choices=["ollama", "azure_foundry"], default="ollama")
    ap.add_argument("--suite", nargs="+", default=["router", "speak", "retrieval"],
                    choices=["router", "speak", "retrieval"])
    ap.add_argument("--write", type=Path, help="append the markdown report to this file")
    args = ap.parse_args()
    asyncio.run(main_async(args.provider, args.suite, args.write))


if __name__ == "__main__":
    main()
