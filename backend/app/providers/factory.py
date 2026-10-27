"""Pick concrete providers from Settings. The ONLY module that imports implementations.

Heavy / optional dependencies (chromadb, azure-*) are imported lazily so a cloud
deployment does not need chromadb and a laptop does not need the Azure SDKs.
"""

from __future__ import annotations

from app.config import Settings
from app.providers.base import LLMProvider, SecretsProvider, VectorStore


def make_secrets(settings: Settings) -> SecretsProvider:
    if settings.secrets_provider == "keyvault":
        from app.providers.secrets_keyvault import KeyVaultSecrets

        return KeyVaultSecrets(settings.azure_keyvault_url)
    from app.providers.secrets_env import EnvSecrets

    return EnvSecrets()


def make_llm(settings: Settings, secrets: SecretsProvider) -> LLMProvider:
    if settings.llm_provider == "ollama":
        from app.providers.ollama import OllamaProvider

        return OllamaProvider(settings)
    from app.providers.azure_foundry import AzureFoundryProvider

    return AzureFoundryProvider(settings, secrets)


def make_vector_store(settings: Settings, secrets: SecretsProvider) -> VectorStore:
    if settings.vector_provider == "azure_search":
        from app.providers.azure_search_store import AzureSearchVectorStore

        return AzureSearchVectorStore(settings, secrets)
    if settings.vector_provider == "memory":
        from app.providers.memory_store import MemoryVectorStore

        return MemoryVectorStore()
    from app.providers.chroma_store import ChromaVectorStore

    return ChromaVectorStore(settings.chroma_path)
