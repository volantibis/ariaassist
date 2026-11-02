# Local setup (Apple Silicon, 8 GB)

Tool names, flags, and config formats change between releases. If something here
does not match what you see, check the tool's current docs.

## 1. Ollama and memory settings

```bash
brew install ollama            # or the macOS app
export OLLAMA_MAX_LOADED_MODELS=1   # swap models instead of holding two
export OLLAMA_KEEP_ALIVE=2m         # free memory soon after use
ollama serve
```

Put the two exports in `~/.zshrc`. If you use the macOS app, set them with
`launchctl setenv` and restart the app.

## 2. Models

Run `ollama list` to see what you already have, then align names in `.env`.

```bash
ollama pull qwen3:4b                # agent brain (fallback: qwen3:1.7b)
ollama pull qwen2.5-coder:1.5b      # Continue autocomplete (keep only this coder model on 8 GB)
# You already have: nomic-embed-text v1.5 and qwen3-vl-2b-instruct (8-bit).
# If they are GGUF files rather than Ollama models, import them with a Modelfile:
#   FROM ./your-model.gguf
#   then: ollama create <name> -f Modelfile
```

Tip: close browser tabs and quit Docker before loading a model. If macOS starts
swapping, generation slows to a crawl.

### Larger context for OpenCode

Ollama's default context is small. Create a variant (start at 8k; try 16k only if
memory allows):

```
# Modelfile.qwen3-4b-8k
FROM qwen3:4b
PARAMETER num_ctx 8192
```

```bash
ollama create qwen3-4b-8k -f Modelfile.qwen3-4b-8k
```

## 3. Continue (VS Code)

`~/.continue/config.yaml`:

```yaml
name: Local
version: 1.0.0
schema: v1
models:
  - name: Qwen3 4B
    provider: ollama
    model: qwen3:4b
    roles: [chat, edit, apply]
  - name: Qwen2.5 Coder 1.5B
    provider: ollama
    model: qwen2.5-coder:1.5b
    roles: [autocomplete]
```

Use it for tab completion, "explain this", and small inline edits.

## 4. OpenCode (terminal)

`opencode.json` in the repo root (or the global config):

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "ollama": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Ollama (local)",
      "options": { "baseURL": "http://localhost:11434/v1" },
      "models": {
        "qwen3-4b-8k": { "name": "Qwen3 4B (8k ctx)" }
      }
    }
  }
}
```

Use it for contained single-file tasks: unit tests, docstrings, one React
component, a small refactor.

## 5. Which tool for what

| Task type | Tool |
|---|---|
| Multi-file features, LangGraph agent, WebSockets, Docker, Azure deploy, debugging | Claude Code (reads `CLAUDE.md`) |
| Autocomplete, explain, small inline edit | Continue |
| Tests/docstrings/boilerplate for one file | OpenCode |

Learning exercise: give Continue and OpenCode the same small task (for example,
tests for `config.py`) and compare. Note where each one fails; it is good
interview material.

## 6. Run the app

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-local.txt -r requirements-dev.txt
cp ../.env.example ../.env          # then align the OLLAMA_* names with `ollama list`
uvicorn app.main:app --reload       # terminal 1, port 8000
```

```bash
cd frontend && npm install && npm run dev    # terminal 2, open http://localhost:5173
```

Open two browser windows with different names. Try, in order:
1. `@Aria hello` (plain chat)
2. Attach `backend/eval/docs/company_handbook.md`, then `@Aria how many days of annual leave do I get?`
   (answer with a citation chip)
3. `@Aria find us a cheap italian place`, then `@Aria book Bella Notte for 4 at 8pm` and click Approve
4. Send 4 messages about dinner without mentioning Aria; she may offer restaurants on her own

If Aria answers "couldn't reach my language model", check `ollama serve` is running and that the model
names in `.env` match `ollama list`. First replies are slow while a model loads.

## 7. Tests and evaluation

```bash
cd backend
pytest -q && ruff check .                          # fast, no models needed
python -m eval.run --provider ollama --write ../docs/EVAL.md
```

The eval swaps models in and out of memory, so close other heavy apps first. Expect a small local model to
score lower than a hosted one; that gap is a useful README result.

## 8. Using the coding agents

See `docs/AGENT_WORKFLOW.md` for a codebase reading order, the stub-first recipe for inline completion,
and an exercise ladder per tool.
