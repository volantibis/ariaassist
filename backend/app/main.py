"""FastAPI app: WebSocket chat, document upload, and small read-only REST endpoints."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Annotated, Any

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.agent.graph import build_graph
from app.agent.service import AriaService
from app.agent.state import AgentDeps
from app.chat.room import Message, Room
from app.config import Settings, get_settings
from app.db import Database
from app.ingest.loaders import UnsupportedFileError
from app.ingest.pipeline import ingest_file
from app.providers.base import LLMProvider, ModelNotFoundError, VectorStore
from app.providers.factory import make_llm, make_secrets, make_vector_store

MAX_TEXT = 2000
CLOSE_BAD_NAME = 4400
CLOSE_NAME_TAKEN = 4409


def _ollama_error_hint(response: httpx.Response) -> str:
    """Extract a user-visible hint from an Ollama error response body.

    Ollama returns JSON like {"error":"{\"error\":{\"message\":\"...\"}}"}.
    Returns a sentence fragment or empty string when nothing useful is found.
    """
    try:
        body = response.json()
    except ValueError:
        return ""
    error = body.get("error")
    if isinstance(error, str):
        try:
            error = json.loads(error)
        except ValueError:
            return ""
    if not isinstance(error, dict):
        return ""
    inner = error.get("error")
    if isinstance(inner, str):
        try:
            inner = json.loads(inner)
        except ValueError:
            inner = {}
    if not isinstance(inner, dict):
        return ""
    message = inner.get("message", "")
    if not isinstance(message, str):
        return ""
    if "image input is not supported" in message.lower() or "mmproj" in message.lower():
        return f"Ollama says: {message} (vision projectors may be missing - check your Modelfile includes the mmproj)."
    return f"Ollama says: {message}"


def create_app(
    *,
    settings: Settings | None = None,
    llm: LLMProvider | None = None,
    store: VectorStore | None = None,
    db: Database | None = None,
) -> FastAPI:
    """Build the app. Tests pass fakes; production passes nothing and uses Settings."""
    cfg = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        secrets = make_secrets(cfg)
        app.state.db = db or Database(cfg.sqlite_path)
        app.state.llm = llm or make_llm(cfg, secrets)
        if llm is None:
            await app.state.llm.verify_models()
        app.state.store = store or make_vector_store(cfg, secrets)
        app.state.room = Room(app.state.db)
        await app.state.room.load()
        graph = build_graph(AgentDeps(app.state.llm, app.state.store, app.state.db, cfg))
        app.state.service = AriaService(graph=graph, room=app.state.room, settings=cfg)
        yield
        for component in (app.state.llm, app.state.store):
            close = getattr(component, "aclose", None)
            if close:
                await close()

    app = FastAPI(title="Aria", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ----------------------------------------------------------------- REST

    @app.get("/_debug_settings")
    def debug_settings():
        # 1. Print it to your Uvicorn terminal console to inspect it
        print("DEBUG SETTINGS OBJECT:", cfg, type(cfg))
        
        # 2. Prevent the crash if it's None
        if cfg is None:
            return {"error": "The settings object is None. Check your app/config.py file."}
            
        # 3. Handle both Pydantic v2 (.model_dump) and Pydantic v1 (.dict)
        if hasattr(cfg, "model_dump"):
            return cfg.model_dump()
        elif hasattr(cfg, "dict"):
            return cfg.dict()
            
        # 4. Fallback if it's just a normal object/class
        return vars(cfg)


    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/documents")
    async def documents() -> list[dict[str, Any]]:
        return [asdict(d) for d in await app.state.db.list_documents()]

    @app.get("/api/bookings")
    async def bookings() -> list[dict[str, Any]]:
        return [asdict(b) for b in await app.state.db.list_bookings()]

    @app.post("/api/upload")
    async def upload(
        file: Annotated[UploadFile, File()], uploader: Annotated[str, Form()] = "someone"
    ) -> dict[str, Any]:
        data = await file.read(cfg.max_upload_mb * 1024 * 1024 + 1)
        if len(data) > cfg.max_upload_mb * 1024 * 1024:
            raise HTTPException(413, f"File is larger than {cfg.max_upload_mb} MB")
        service: AriaService = app.state.service
        try:
            async with service.model_lock:  # embedding/captioning shares the model with Aria
                doc = await ingest_file(
                    llm=app.state.llm,
                    store=app.state.store,
                    db=app.state.db,
                    settings=cfg,
                    filename=file.filename or "",
                    data=data,
                    uploaded_by=uploader,
                )
        except UnsupportedFileError as e:
            raise HTTPException(400, str(e)) from e
        except ModelNotFoundError as e:
            raise HTTPException(
                502, f"Ollama model not configured: {e.detail or e.model}"
            ) from e
        except httpx.HTTPStatusError as e:  # Ollama rejected the request, e.g. missing vision support -> 500
            hint = _ollama_error_hint(e.response)
            message = (
                f"Could not process the file: Ollama returned {e.response.status_code}. {hint}"
                if hint
                else f"Could not process the file: Ollama returned {e.response.status_code}. "
                "For images, try uploading a smaller one."
            )
            raise HTTPException(502, message) from e
        except ValueError as e:  # unreadable image file
            raise HTTPException(400, str(e)) from e
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"Could not process the file: {e}") from e
        await app.state.room.add(
            Message(
                author="system",
                kind="system",
                text=f"{uploader} uploaded {doc.name} ({doc.chunks} chunks). Ask @Aria about it.",
            )
        )
        return asdict(doc)

    # ------------------------------------------------------------ WebSocket
    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket, name: str = "") -> None:
        room: Room = app.state.room
        service: AriaService = app.state.service
        name = name.strip()
        await ws.accept()
        if not (1 <= len(name) <= 24):
            await ws.close(code=CLOSE_BAD_NAME)
            return
        if room.name_taken(name):
            await ws.close(code=CLOSE_NAME_TAKEN)
            return
        await room.join(name, ws)
        try:
            while True:
                try:
                    data = json.loads(await ws.receive_text())
                except json.JSONDecodeError:
                    continue
                if not isinstance(data, dict):
                    continue
                if data.get("type") == "confirm":
                    service.spawn(
                        service.handle_confirm(
                            str(data.get("action_id", "")), bool(data.get("approve")), name
                        )
                    )
                    continue
                text = str(data.get("text", "")).strip()[:MAX_TEXT]
                if not text:
                    continue
                msg = Message(author=name, text=text)
                await room.add(msg)
                await service.on_human_message(msg)
        except WebSocketDisconnect:
            pass
        finally:
            await room.leave(name)

    return app


app = create_app()
