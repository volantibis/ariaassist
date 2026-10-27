# Agent instructions (OpenCode and other agents that read AGENTS.md)

Project: Aria, a group chat with an AI member (FastAPI + LangGraph backend, React frontend).
Start with `docs/CODEMAP.md` (function index), `docs/LLD.md` (contracts) and `CLAUDE.md` (full rules).

## Rules
- Python 3.11+, type hints everywhere, async I/O. Match the style of neighbouring files.
- `agent/` and `chat/` import only `providers/base.py` interfaces, never a concrete provider.
  `providers/factory.py` is the only module that imports implementations.
- Read configuration only through `app/config.py` (`Settings`). Secrets only through `SecretsProvider`.
- Every behaviour change needs a test in `backend/tests/` (fakes only: no network, no real models).
- Prompts and JSON schemas live in `agent/prompts.py`. Unknown values in schemas are `""` or `0`, not null.
- Keep functions under about 25 lines. Prefer editing one file at a time.

## Commands
- Tests: `cd backend && pytest -q`      Lint: `cd backend && ruff check .`
- Frontend build check: `cd frontend && npm run build`
- Regenerate code map after structural changes: `python scripts/gen_codemap.py`

## Hardware note
Developer machine has 8 GB RAM; local models are small (qwen3:4b, nomic-embed-text, qwen3-vl 2B).
Never add heavy dependencies or run several models at once.
