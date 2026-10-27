#!/usr/bin/env bash
# Deploy Aria to Azure: ACR + App Service (container) + Key Vault + AI Search + model deployments
# + Static Web Apps. Written from the az CLI docs; it was NOT executed by its author, so read each
# step, check flags with `az <command> -h`, and run it block by block the first time.
#
# Prerequisites: az CLI logged in (`az login`), Node 20+ (for the SWA CLI), a subscription with
# Azure OpenAI access and quota in $LOCATION. Cost: App Service B1 + ACR Basic run continuously;
# tear down with `az group delete -n $RG --yes --no-wait` when you are done.
set -euo pipefail

# ------------------------------------------------------------------ edit these
PREFIX="${PREFIX:-aria$RANDOM}"        # lowercase letters/digits; makes global names unique
LOCATION="${LOCATION:-eastus}"         # must offer the models below
SWA_LOCATION="${SWA_LOCATION:-eastus2}" # Static Web Apps supports a limited set of regions
RG="rg-aria"
CHAT_MODEL="gpt-4o-mini";            CHAT_VERSION="2024-07-18"
EMBED_MODEL="text-embedding-3-small"; EMBED_VERSION="1"
SEARCH_SKU="free"                      # one free search service per subscription; use basic otherwise
# ----------------------------------------------------------------------------


ACR_NAME="${PREFIX}acr"; PLAN="asp-aria"; WEBAPP="${PREFIX}-api"; KV="${PREFIX}-kv"
SEARCH="${PREFIX}-search"; AI="${PREFIX}-ai"; SWA="${PREFIX}-web"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
echo "Using prefix: $PREFIX"

# Check global availability
REGISTRY_AVAILABLE=$(az acr check-name-availability --name "$ACR_NAME" --query nameAvailable --output tsv)

if [ "$REGISTRY_AVAILABLE" = "true" ]; then
    echo "Registry name '$ACR_NAME' is available. Creating resource..."
    az acr create --resource-group "$RESOURCE_GROUP" --name "$ACR_NAME" --sku Basic
else
    echo "Registry name '$ACR_NAME' is already claimed. Verifying ownership..."
    
    # Check if it belongs to your subscription
    OUR_REGISTRY=$(az acr list --query "[?name=='$ACR_NAME'].name" --output tsv)
    
    if [ -n "$OUR_REGISTRY" ]; then
        echo "Found existing registry belonging to your subscription. Skipping creation step."
    else
        echo "ERROR: Registry name '$ACR_NAME' is globally taken by someone else."
        echo "Action required: Change the ACR_NAME variable in this script to something unique."
        exit 1
    fi
fi
echo "Proceeding to container build stage..."

echo "== 1. Resource group"
az group create -n "$RG" -l "$LOCATION" -o none

echo "== 2. Container registry, and build the image in Azure (no local Docker needed)"
az acr create -g "$RG" -n "$ACR" --sku Basic -o none
# az acr build -r "$ACR" -t aria-backend:latest "$ROOT/backend"

echo "== 3. Model resource and deployments (Azure OpenAI endpoint, usable from AI Foundry)"
az cognitiveservices account create -g "$RG" -n "$AI" --kind OpenAI --sku S0 -l "$LOCATION" \
  --custom-domain "$AI" -o none
az cognitiveservices account deployment create -g "$RG" -n "$AI" --deployment-name chat \
  --model-name "$CHAT_MODEL" --model-version "$CHAT_VERSION" --model-format OpenAI \
  --sku-name Standard --sku-capacity 10 -o none
az cognitiveservices account deployment create -g "$RG" -n "$AI" --deployment-name embed \
  --model-name "$EMBED_MODEL" --model-version "$EMBED_VERSION" --model-format OpenAI \
  --sku-name Standard --sku-capacity 10 -o none
AI_ENDPOINT="$(az cognitiveservices account show -g "$RG" -n "$AI" --query properties.endpoint -o tsv)"
AI_KEY="$(az cognitiveservices account keys list -g "$RG" -n "$AI" --query key1 -o tsv)"

echo "== 4. AI Search"
az search service create -g "$RG" -n "$SEARCH" --sku "$SEARCH_SKU" -l "$LOCATION" -o none
SEARCH_KEY="$(az search admin-key show -g "$RG" --service-name "$SEARCH" --query primaryKey -o tsv)"

echo "== 5. Key Vault (RBAC mode) and secrets"
az keyvault create -g "$RG" -n "$KV" -l "$LOCATION" --enable-rbac-authorization true -o none
KV_ID="$(az keyvault show -n "$KV" --query id -o tsv)"
ME="$(az ad signed-in-user show --query id -o tsv)"
az role assignment create --assignee "$ME" --role "Key Vault Secrets Officer" --scope "$KV_ID" -o none
echo "   waiting for role assignment to propagate..."; sleep 45
az keyvault secret set --vault-name "$KV" -n azure-foundry-api-key --value "$AI_KEY" -o none
az keyvault secret set --vault-name "$KV" -n azure-search-key --value "$SEARCH_KEY" -o none

echo "== 6. App Service running the container, with a managed identity"
az appservice plan create -g "$RG" -n "$PLAN" --is-linux --sku B1 -o none
ACR_ID="$(az acr show -n "$ACR" --query id -o tsv)"
az webapp create -g "$RG" -p "$PLAN" -n "$WEBAPP" \
  --deployment-container-image-name "$ACR.azurecr.io/aria-backend:latest" -o none
PRINCIPAL="$(az webapp identity assign -g "$RG" -n "$WEBAPP" --query principalId -o tsv)"
az role assignment create --assignee "$PRINCIPAL" --role AcrPull --scope "$ACR_ID" -o none
az role assignment create --assignee "$PRINCIPAL" --role "Key Vault Secrets User" --scope "$KV_ID" -o none
az webapp config set -g "$RG" -n "$WEBAPP" --web-sockets-enabled true --always-on true \
  --generic-configurations '{"acrUseManagedIdentityCreds": true}' -o none

echo "== 7. Static Web App for the React build"
az staticwebapp create -g "$RG" -n "$SWA" -l "$SWA_LOCATION" --sku Free -o none
SWA_HOST="$(az staticwebapp show -g "$RG" -n "$SWA" --query defaultHostname -o tsv)"

echo "== 8. App settings (no secrets here: keys come from Key Vault at runtime)"
az webapp config appsettings set -g "$RG" -n "$WEBAPP" -o none --settings \
  WEBSITES_PORT=8000 \
  LLM_PROVIDER=azure_foundry VECTOR_PROVIDER=azure_search SECRETS_PROVIDER=keyvault \
  AZURE_FOUNDRY_ENDPOINT="${AI_ENDPOINT%/}" AZURE_FOUNDRY_CHAT_DEPLOYMENT=chat \
  AZURE_FOUNDRY_EMBED_DEPLOYMENT=embed AZURE_EMBED_DIM=1536 \
  AZURE_SEARCH_ENDPOINT="https://${SEARCH}.search.windows.net" AZURE_SEARCH_INDEX=aria-docs \
  AZURE_KEYVAULT_URL="https://${KV}.vault.azure.net" \
  SQLITE_PATH=/home/aria.db UPLOAD_DIR=/home/uploads \
  CORS_ORIGINS="https://${SWA_HOST}"
az webapp restart -g "$RG" -n "$WEBAPP"

echo "== 9. Build and deploy the frontend"
( cd "$ROOT/frontend" && npm ci && VITE_API_URL="https://${WEBAPP}.azurewebsites.net" npm run build )
SWA_TOKEN="$(az staticwebapp secrets list -g "$RG" -n "$SWA" --query properties.apiKey -o tsv)"
npx --yes @azure/static-web-apps-cli deploy "$ROOT/frontend/dist" \
  --deployment-token "$SWA_TOKEN" --env production

cat <<EOF

Done.
  Frontend : https://${SWA_HOST}
  Backend  : https://${WEBAPP}.azurewebsites.net/health
  Logs     : az webapp log tail -g $RG -n $WEBAPP
  Teardown : az group delete -n $RG --yes --no-wait
EOF
