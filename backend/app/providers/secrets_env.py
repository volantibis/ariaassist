"""SecretsProvider backed by environment variables / .env (local development)."""

from __future__ import annotations

import os

from dotenv import dotenv_values

from app.providers.base import SecretsProvider


class EnvSecrets(SecretsProvider):
    """`get("azure-foundry-api-key")` reads env var AZURE_FOUNDRY_API_KEY.

    Key Vault secret names allow only letters, digits and dashes, so the shared naming
    convention is dash-separated; this class maps it to the usual env var spelling.
    """

    def __init__(self, env_file: str | tuple[str, ...] = (".env", "../.env")) -> None:
        files = (env_file,) if isinstance(env_file, str) else env_file
        self._file: dict[str, str] = {}
        for path in files:  # later files do not override earlier ones
            for key, value in dotenv_values(path).items():
                if value:
                    self._file.setdefault(key, value)

    def get(self, name: str) -> str:
        key = name.upper().replace("-", "_")
        value = os.environ.get(key) or self._file.get(key)
        if not value:
            raise KeyError(f"secret '{name}' not found (looked for env var {key})")
        return value
