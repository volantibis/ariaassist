#!/usr/bin/env bash
# Deploy Aria to the EXISTING Azure infrastructure.
# Backend: Azure Container Apps
# Image: Azure Container Registry
# LLM: Azure AI Foundry
# Vector search: Azure AI Search
# Secrets: Azure Key Vault
# Frontend: deployed separately by GitHub Actions / Static Web Apps
#
# Run from any directory:
#   bash scripts/deploy_azure.sh
#
# Prerequisites:
#   - Azure CLI logged in to the intended subscription
#   - Permission to build in ACR and assign Azure RBAC roles
#   - Permission to read/create the required Key Vault secrets

set -Eeuo pipefail

# -------------------------------------------------------------------
# Existing infrastructure — do not generate new resource names here.
# -------------------------------------------------------------------

RG="rg-aria"
LOCATION="eastus"

ACR="agentariaacr"
APP="agentaria"
KV="agentaria"
SEARCH="as-agentaria"
FOUNDRY="agentaria-foundry"

SWA_ORIGIN="https://agreeable-forest-06d9f9500.4.azurestaticapps.net"

FOUNDRY_ENDPOINT="https://${FOUNDRY}.cognitiveservices.azure.com/"
FOUNDRY_CHAT_DEPLOYMENT="gpt-4.1-mini"
FOUNDRY_EMBED_DEPLOYMENT="text-embedding-3-small"
FOUNDRY_API_VERSION="2024-10-21"

SEARCH_INDEX="aria-docs"
EMBED_DIM="1536"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TAG="deploy-$(date -u +%Y%m%d%H%M%S)"
IMAGE="${ACR}.azurecr.io/aria-backend:${TAG}"
BACKEND_URL="https://${APP}.kindplant-340166a2.eastus.azurecontainerapps.io"

echo "Deploying Aria"
echo "Resource group: $RG"
echo "Container App:  $APP"
echo "Image:          $IMAGE"

# -------------------------------------------------------------------
# 1. Validate existing resources and Azure subscription.
# -------------------------------------------------------------------

echo "== 1. Validate Azure resources"

az account show --output table

az group show \
  --name "$RG" \
  --output none

az acr show \
  --name "$ACR" \
  --resource-group "$RG" \
  --output none

az containerapp show \
  --name "$APP" \
  --resource-group "$RG" \
  --output none

az keyvault show \
  --name "$KV" \
  --output none

az search service show \
  --name "$SEARCH" \
  --resource-group "$RG" \
  --output none

az cognitiveservices account show \
  --name "$FOUNDRY" \
  --resource-group "$RG" \
  --output none

echo "Existing resources verified."

# -------------------------------------------------------------------
# 2. Ensure ACR authentication and managed-identity permissions.
# -------------------------------------------------------------------

echo "== 2. Configure container registry access"

ACR_ID="$(az acr show \
  --name "$ACR" \
  --resource-group "$RG" \
  --query id -o tsv)"

ACR_AUTH="$(az acr config authentication-as-arm show \
  --registry "$ACR" \
  --query status -o tsv)"

if [[ "$ACR_AUTH" != "enabled" ]]; then
  az acr config authentication-as-arm update \
    --registry "$ACR" \
    --status enabled
fi

az containerapp registry set \
  --name "$APP" \
  --resource-group "$RG" \
  --server "${ACR}.azurecr.io" \
  --identity system \
  --output none

PRINCIPAL_ID="$(az containerapp show \
  --name "$APP" \
  --resource-group "$RG" \
  --query identity.principalId -o tsv)"

if [[ -z "$PRINCIPAL_ID" || "$PRINCIPAL_ID" == "None" ]]; then
  echo "ERROR: Container App system-assigned identity is missing." >&2
  exit 1
fi

HAS_ACR_PULL="$(az role assignment list \
  --assignee "$PRINCIPAL_ID" \
  --scope "$ACR_ID" \
  --query "[?roleDefinitionName=='AcrPull'] | length(@)" \
  -o tsv)"

if [[ "$HAS_ACR_PULL" == "0" ]]; then
  az role assignment create \
    --assignee "$PRINCIPAL_ID" \
    --role "AcrPull" \
    --scope "$ACR_ID" \
    --output none
  echo "Assigned AcrPull to Container App identity."
else
  echo "AcrPull assignment already exists."
fi

# -------------------------------------------------------------------
# 3. Ensure Key Vault secret aliases exist.
#    Never print secret values.
# -------------------------------------------------------------------

echo "== 3. Verify Key Vault secret aliases"

KV_ID="$(az keyvault show \
  --name "$KV" \
  --query id -o tsv)"

ensure_secret_alias() {
  local source_name="$1"
  local target_name="$2"
  local secret_value

  if az keyvault secret show \
    --vault-name "$KV" \
    --name "$target_name" \
    --query id -o tsv >/dev/null 2>&1; then
    echo "Secret alias exists: $target_name"
    return
  fi

  echo "Creating missing secret alias: $target_name"

  if ! secret_value="$(az keyvault secret show \
    --vault-name "$KV" \
    --name "$source_name" \
    --query value -o tsv)"; then
    echo "ERROR: Cannot read source secret: $source_name" >&2
    return 1
  fi

  if [[ -z "$secret_value" ]]; then
    echo "ERROR: Source secret is empty: $source_name" >&2
    unset secret_value
    return 1
  fi

  az keyvault secret set \
    --vault-name "$KV" \
    --name "$target_name" \
    --value "$secret_value" \
    --query id -o tsv >/dev/null

  unset secret_value
}

ensure_secret_alias "Foundry-API-Key" "azure-foundry-api-key"
ensure_secret_alias "Foundry-Search-Key" "azure-search-key"

HAS_KV_ACCESS="$(az role assignment list \
  --assignee "$PRINCIPAL_ID" \
  --scope "$KV_ID" \
  --query "[?roleDefinitionName=='Key Vault Secrets User'] | length(@)" \
  -o tsv)"

if [[ "$HAS_KV_ACCESS" == "0" ]]; then
  az role assignment create \
    --assignee "$PRINCIPAL_ID" \
    --role "Key Vault Secrets User" \
    --scope "$KV_ID" \
    --output none
  echo "Assigned Key Vault Secrets User to Container App identity."
else
  echo "Key Vault Secrets User assignment already exists."
fi

# -------------------------------------------------------------------
# 4. Build a uniquely tagged image directly in ACR.
# -------------------------------------------------------------------

echo "== 4. Build and push backend image"

az acr build \
  --registry "$ACR" \
  --image "aria-backend:${TAG}" \
  "$ROOT/backend"

echo "Image build completed: $IMAGE"

# -------------------------------------------------------------------
# 5. Update the existing Container App.
#    No App Service or duplicate AI resources are created.
# -------------------------------------------------------------------

echo "== 5. Deploy backend revision"

az containerapp update \
  --name "$APP" \
  --resource-group "$RG" \
  --image "$IMAGE" \
  --set-env-vars \
    "LLM_PROVIDER=azure_foundry" \
    "VECTOR_PROVIDER=azure_search" \
    "SECRETS_PROVIDER=keyvault" \
    "AZURE_FOUNDRY_ENDPOINT=${FOUNDRY_ENDPOINT}" \
    "AZURE_FOUNDRY_CHAT_DEPLOYMENT=${FOUNDRY_CHAT_DEPLOYMENT}" \
    "AZURE_FOUNDRY_EMBED_DEPLOYMENT=${FOUNDRY_EMBED_DEPLOYMENT}" \
    "AZURE_FOUNDRY_API_VERSION=${FOUNDRY_API_VERSION}" \
    "AZURE_EMBED_DIM=${EMBED_DIM}" \
    "AZURE_SEARCH_ENDPOINT=https://${SEARCH}.search.windows.net" \
    "AZURE_SEARCH_INDEX=${SEARCH_INDEX}" \
    "AZURE_KEYVAULT_URL=https://${KV}.vault.azure.net/" \
    "CORS_ORIGINS=${SWA_ORIGIN}" \
    "ARIA_DEPLOYMENT_REV=${TAG}"

# -------------------------------------------------------------------
# 6. Inspect revision status and test health.
# -------------------------------------------------------------------

echo "== 6. Check Container App revision"

az containerapp revision list \
  --name "$APP" \
  --resource-group "$RG" \
  --output table

echo "Waiting briefly for the new revision to start..."
sleep 15

echo "== 7. Test backend health"

if curl --fail --silent --show-error \
  "${BACKEND_URL}/health"; then
  echo
  echo "Backend health endpoint responded successfully."
else
  echo
  echo "WARNING: Health check failed. Inspect revision status and logs."
  echo "Logs:"
  az containerapp logs show \
    --name "$APP" \
    --resource-group "$RG" \
    --type console \
    --tail 50 || true
  exit 1
fi

# -------------------------------------------------------------------
# 7. Frontend deployment is handled by the existing GitHub workflow.
# -------------------------------------------------------------------

cat <<EOF

Backend deployment submitted.

Image:    ${IMAGE}
Backend:  ${BACKEND_URL}
Health:   ${BACKEND_URL}/health
Frontend: ${SWA_ORIGIN}

The frontend is deployed separately by GitHub Actions.
Ensure repository variable VITE_API_URL is set to:
${BACKEND_URL}

Then push frontend/workflow changes to main and verify the
Static Web Apps workflow succeeds.

EOF
