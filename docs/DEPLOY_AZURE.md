# Deploying to Azure

`scripts/deploy_azure.sh` automates everything below. **It was written from documentation and has not
been run by its author**: read it, run it block by block the first time, and check flags with
`az <command> -h` because the CLI changes. Verify prices and free-tier limits on the Azure pricing pages.

## What gets created

| Resource | Purpose | Cost note |
|---|---|---|
| Resource group `rg-aria` | One thing to delete when finished | free |
| Container Registry (Basic) | Stores the backend image; `az acr build` builds it in Azure | small monthly fee |
| Azure OpenAI / Foundry resource + 2 deployments | `chat` (gpt-4o-mini class) and `embed` (text-embedding-3-small) | pay per token, cents for a demo |
| AI Search (free tier) | Vector index. Free tier has small storage and no SLA | free (one per subscription) |
| Key Vault (RBAC) | `azure-foundry-api-key`, `azure-search-key` | almost nothing |
| App Service plan B1 (Linux) + web app | Runs the container; WebSockets on; managed identity | the main running cost |
| Static Web App (Free) | Serves the React build | free |

Trial credits last a limited time. After they end, paid resources stop unless you upgrade. Decide in
advance what stays live; the repo, README and demo video are the permanent portfolio.

## Order of operations

1. Deploy models in the Foundry portal or via CLI; copy the **endpoint** (`https://<name>.openai.azure.com`),
   the two **deployment names** and a **key**. The backend needs only those.
2. Build and push the image (`az acr build`), create the web app, give its managed identity
   `AcrPull` on the registry and `Key Vault Secrets User` on the vault.
3. Put the two secrets in Key Vault. The app reads them at runtime through `KeyVaultSecrets`
   (`DefaultAzureCredential`). Nothing secret is in app settings, the image, or git.
4. Set app settings (the script prints them): provider selectors, endpoints, `WEBSITES_PORT=8000`,
   `SQLITE_PATH=/home/aria.db` and `UPLOAD_DIR=/home/uploads` (on Linux App Service `/home` is persistent),
   and `CORS_ORIGINS=https://<your-static-site>`.
5. Build the frontend with `VITE_API_URL=https://<webapp>.azurewebsites.net` and deploy `dist/` to Static Web Apps.
   The browser connects to the web app directly for WebSockets.

## Verify

```bash
curl https://<webapp>.azurewebsites.net/health        # {"status":"ok"}
az webapp log tail -g rg-aria -n <webapp>             # watch startup and errors
```

Open the static site, join, upload a PDF, ask `@Aria` a question about it.

## Run the eval against the hosted model

Locally, with the Azure settings in your `.env` and `SECRETS_PROVIDER=env` (put the two keys in `.env`
as `AZURE_FOUNDRY_API_KEY=...`):

```bash
cd backend
python -m eval.run --provider ollama        --write ../docs/EVAL.md
python -m eval.run --provider azure_foundry --write ../docs/EVAL.md
```

The two tables side by side (local 4B versus hosted model) are a strong README section.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Container does not start | `WEBSITES_PORT` missing; check `az webapp log tail`; image not pulled (identity lacks `AcrPull`, or `acrUseManagedIdentityCreds` not set) |
| Chat shows "closed" immediately | Web sockets not enabled on the web app, or `VITE_API_URL` points at the wrong host (must be https so the socket is wss) |
| Browser console CORS error on upload | `CORS_ORIGINS` does not exactly match the static site origin (scheme and host, no trailing slash) |
| Upload returns 502 mentioning "dim" | Index was created with another embedding size; recreate the index and re-embed (`reindex_all`) |
| `KeyError: secret ... not available in Key Vault` | Role assignment not propagated yet (wait a few minutes) or secret name misspelled |
| Aria replies "couldn't reach my language model" | Wrong endpoint or deployment name, or quota exhausted; check the log line from `aria.service` |

## Limits to mention in the README

Single backend instance (room state is in memory); SQLite on App Service storage; pending booking
confirmations are lost on restart; no authentication.
