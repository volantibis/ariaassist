"""All configuration. Read from environment / .env; never read os.environ elsewhere."""

from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    # --- which implementation to use -------------------------------------
    llm_provider: Literal["ollama", "azure_foundry"] = "ollama"
    vector_provider: Literal["chroma", "azure_search", "memory"] = "chroma"
    secrets_provider: Literal["env", "keyvault"] = "env"

    # --- local: Ollama ----------------------------------------------------
    ollama_base_url: str = "http://localhost:11434"
    ollama_chat_model: str = "qwen3:4b"
    ollama_embed_model: str = "nomic-embed-text"
    ollama_vision_model: str = "qwen3:4b"

    # --- local: storage ---------------------------------------------------
    chroma_path: str = "./.chroma"
    sqlite_path: str = "./aria.db"
    upload_dir: str = "./uploads"
    max_upload_mb: int = 10

    # --- cloud: Azure -----------------------------------------------------
    azure_foundry_endpoint: str = ""  # https://<resource>.openai.azure.com
    azure_foundry_chat_deployment: str = ""  # e.g. a gpt-4o-mini deployment name
    azure_foundry_embed_deployment: str = ""  # e.g. a text-embedding-3-small deployment
    azure_foundry_api_version: str = "2024-10-21"
    azure_embed_dim: int = 1536  # text-embedding-3-small default
    azure_search_endpoint: str = ""  # https://<service>.search.windows.net
    azure_search_index: str = "aria-docs"
    azure_keyvault_url: str = ""  # https://<vault>.vault.azure.net

    # --- agent behaviour --------------------------------------------------
    retrieval_k: int = 4
    retrieval_min_score: float = 0.25  # drop hits less similar than this (cosine)
    proactive_enabled: bool = True
    proactive_every_n: int = 4  # consider speaking after this many human messages
    proactive_cooldown_s: int = 90

    # --- web --------------------------------------------------------------
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


def get_settings() -> Settings:
    return Settings()
