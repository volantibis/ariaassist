"""SecretsProvider backed by Azure Key Vault (production).

Authenticates with DefaultAzureCredential: the App Service managed identity in Azure,
or your `az login` session on your laptop. No keys live in code, env or the image.
Needs the `azure-identity` and `azure-keyvault-secrets` packages (requirements-azure.txt).
"""

from __future__ import annotations

from app.providers.base import SecretsProvider


class KeyVaultSecrets(SecretsProvider):
    def __init__(self, vault_url: str) -> None:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient

        if not vault_url:
            raise ValueError("AZURE_KEYVAULT_URL is required when SECRETS_PROVIDER=keyvault")
        self._client = SecretClient(vault_url=vault_url, credential=DefaultAzureCredential())
        self._cache: dict[str, str] = {}

    def get(self, name: str) -> str:
        if name not in self._cache:
            try:
                self._cache[name] = self._client.get_secret(name).value or ""
            except Exception as exc:  # azure.core.exceptions.ResourceNotFoundError etc.
                raise KeyError(f"secret '{name}' not available in Key Vault: {exc}") from exc
        return self._cache[name]
