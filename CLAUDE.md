# Aria: group chat with an AI agent as a member

Portfolio project for an AI/ML engineer role. A real-time group chat where an AI agent ("Aria") is a
participant: answers @mentions, answers questions over uploaded PDFs/text/images with citations
(multimodal RAG), summarizes the chat, speaks up unprompted when useful, and books tables (mocked but
functional) only after a human clicks Approve.

Read first: `docs/HLD.md` (big picture), `docs/LLD.md` (contracts), `docs/CODEMAP.md` (function index),
`docs/AGENT_WORKFLOW.md` (how to work with coding agents here).

## Hard constraints

- Dev machine: Apple Silicon, **8 GB unified memory**. One Ollama model loaded at a time
  (`OLLAMA_MAX_LOADED_MODELS=1`). No Docker Desktop during development. Chroma runs embedded.
- Everything runs locally at zero cost; the same code runs on Azure by changing config only.
- Keep dependencies small. Prefer plain Python over frameworks when the framework adds nothing.

## Architecture in brief

```
React (Vite) --WebSocket/REST--> FastAPI --> Room --> AriaService --> LangGraph agent --> tools
                                    |                                      |
                                 ingestion                     LLMProvider / VectorStore / SecretsProvider
                                                    (ollama|azure_foundry) (chroma|azure_search|memory) (env|keyvault)
```

- **Dependency rule:** `agent/` and `chat/` import only the interfaces in `providers/base.py`.
  `providers/factory.py` is the only module that imports concrete providers.
- **Agent:** router + skills graph (`agent/graph.py`): ask_docs, summarize, find_restaurant, chitchat,
  book_table (extract_slots -> confirm via LangGraph `interrupt()` -> execute | cancel), and a proactive
  path (`decide_speak`). One narrow structured-JSON decision per node, because small local models fail
  at long multi-goal prompts. Unknown values in schemas are `""` or `0`, never null.
- **Models (local):** agent `qwen3:4b`; embeddings `nomic-embed-text` v1.5 (768-d, prefixes
  `search_document: ` / `search_query: ` are added inside `OllamaProvider.embed`); images are captioned
  by `qwen3-vl` 2B then embedded as text.
- **Embedding dimension differs per backend** (768 local, 1536 Azure). Indexes refuse a dim mismatch;
  `ingest.pipeline.reindex_all` recreates and re-embeds. Never mix vectors from different embedders.
- **Confirm node:** `confirm` must do nothing except call `interrupt()` (the node re-runs from its first
  line on resume).

## Conventions

- Python 3.11+, FastAPI, Pydantic v2, pytest, ruff. Type hints everywhere; async I/O; short functions.
- Config only via `app/config.py`; secrets only via `SecretsProvider` (dash-separated names such as
  `azure-foundry-api-key`). Never commit `.env`.
- Tests use fakes (`backend/tests/conftest.py`: `FakeLLM`, hashing embedder, `MemoryVectorStore`). No
  network, no real models in tests.
- Every agent behaviour change gets cases in `backend/eval/cases.jsonl`; record results in `docs/EVAL.md`.
- Frontend: React + TypeScript + Vite, plain CSS; state and socket logic in `useChat.ts`.
- After structural changes run `python scripts/gen_codemap.py`.
- Small commits, one feature per branch. Update `docs/MILESTONES.md`.

## Commands

- Backend: `cd backend && uvicorn app.main:app --reload` (port 8000)
- Tests / lint: `cd backend && pytest -q` / `ruff check .`
- Eval: `cd backend && python -m eval.run --provider ollama` (or `azure_foundry`; `--write ../docs/EVAL.md`)
- Frontend: `cd frontend && npm install && npm run dev` (port 5173; `VITE_API_URL` / `VITE_WS_URL` override
  `http://localhost:8000`); check build with `npm run build`
- Deploy: `docs/DEPLOY_AZURE.md`, `scripts/deploy_azure.sh` (untested script: read before running)

## Protocol quick reference

WebSocket `/ws?name=<1-24 chars>`; close 4400 bad name, 4409 name taken.
Client sends `{"type":"message","text":...}` or `{"type":"confirm","action_id":...,"approve":bool}`.
Server sends `history`, `message`, `message_update`, `presence`, `typing`. REST: `/health`,
`POST /api/upload`, `GET /api/documents`, `GET /api/bookings`.
