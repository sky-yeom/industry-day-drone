#!/usr/bin/env bash
# Deploys the relay + web dashboard to Azure Container Apps, wired to the
# existing Azure AI Foundry Voice Live resource and an Azure Vision
# deployment. Requires: az CLI, logged in (`az login`), Contributor +
# User Access Administrator on the target resource group (for the role
# assignment this script creates on the Vision resource).
#
# Usage:
#   DEPLOY_CLOUD_APPS=true ./scripts/deploy.sh
# Cloud hosting is retired. Explicit opt-in is required to recreate it.
#
# Override any of these via environment variables before running:
#   RESOURCE_GROUP, LOCATION, ACR_NAME, SITE_PIN,
#   VISION_RESOURCE_GROUP, VISION_RESOURCE_NAME, VISION_DEPLOYMENT
set -euo pipefail

if [[ "${DEPLOY_CLOUD_APPS:-false}" != "true" ]]; then
  echo "Cloud deployment is retired and disabled. No build or Azure changes were made." >&2
  echo "To intentionally recreate the cloud apps, run: DEPLOY_CLOUD_APPS=true ./scripts/deploy.sh" >&2
  exit 1
fi

RESOURCE_GROUP="${RESOURCE_GROUP:-industry-day-drone}"
LOCATION="${LOCATION:-southeastasia}"
ACR_NAME="${ACR_NAME:-industrydaydrone}"
VOICE_LIVE_RESOURCE_NAME="${VOICE_LIVE_RESOURCE_NAME:-industry-day-drone-boot-resource}"
VOICE_LIVE_REGION="${VOICE_LIVE_REGION:-southeastasia}"

# The Vision deployment can live in a different resource group/subscription
# than the demo stack; adjust these if yours does too.
VISION_RESOURCE_GROUP="${VISION_RESOURCE_GROUP:-dev-rg-personal-eastus}"
VISION_RESOURCE_NAME="${VISION_RESOURCE_NAME:-image-detection-testing-resource}"
VISION_DEPLOYMENT="${VISION_DEPLOYMENT:-gpt-5}"

# A shared PIN visitors must enter before reaching the site. Required unless
# you explicitly want it open to anyone with the link (each session calls
# billed Voice Live + Vision APIs). Generates a random one if unset.
SITE_PIN="${SITE_PIN:-$(openssl rand -hex 4)}"

cd "$(dirname "$0")/.."

echo "==> Ensuring Azure Container Registry '$ACR_NAME' exists"
az acr show --name "$ACR_NAME" --resource-group "$RESOURCE_GROUP" >/dev/null 2>&1 || \
  az acr create --name "$ACR_NAME" --resource-group "$RESOURCE_GROUP" \
    --sku Basic --admin-enabled true --location "$LOCATION"

ACR_LOGIN_SERVER=$(az acr show --name "$ACR_NAME" --resource-group "$RESOURCE_GROUP" --query loginServer -o tsv)
IMAGE_TAG="$(date +%Y%m%d%H%M%S)"

# Container Apps FQDNs are deterministic (<app-name>.<environment default
# domain>), so we can compute the relay's public URL before it's ever been
# deployed and bake it into the web image's build (NEXT_PUBLIC_* vars are
# inlined into the client bundle at build time, not read at runtime).
ENV_DEFAULT_DOMAIN=$(az containerapp env show \
  --name "${CONTAINER_APP_ENV:-industry-day-drone-env}" --resource-group "$RESOURCE_GROUP" \
  --query "properties.defaultDomain" -o tsv 2>/dev/null || true)
if [ -z "$ENV_DEFAULT_DOMAIN" ]; then
  echo "==> Container Apps environment doesn't exist yet; creating it first so we know its default domain"
  terraform -chdir=infra init -input=false >/dev/null
  terraform -chdir=infra apply -input=false -auto-approve \
    -var "deploy_cloud_apps=true" \
    -target=azurerm_container_app_environment.main \
    -var "relay_image=mcr.microsoft.com/k8se/quickstart:latest" \
    -var "web_image=mcr.microsoft.com/k8se/quickstart:latest" \
    -var "site_pin=$SITE_PIN" \
    -var "voice_live_resource_group=$RESOURCE_GROUP" \
    -var "voice_live_resource_name=$VOICE_LIVE_RESOURCE_NAME" \
    -var "vision_resource_group=$VISION_RESOURCE_GROUP" \
    -var "vision_resource_name=$VISION_RESOURCE_NAME" \
    -var "vision_deployment=$VISION_DEPLOYMENT"
  ENV_DEFAULT_DOMAIN=$(az containerapp env show \
    --name "${CONTAINER_APP_ENV:-industry-day-drone-env}" --resource-group "$RESOURCE_GROUP" \
    --query "properties.defaultDomain" -o tsv)
fi
RELAY_FQDN="idd-relay.${ENV_DEFAULT_DOMAIN}"

echo "==> Building + pushing relay image (az acr build, no local Docker needed)"
az acr build --registry "$ACR_NAME" --image "idd-relay:$IMAGE_TAG" --file relay/Dockerfile .

echo "==> Building + pushing web image (relay URL baked in: https://$RELAY_FQDN)"
az acr build --registry "$ACR_NAME" --image "idd-web:$IMAGE_TAG" --file Dockerfile \
  --build-arg NEXT_PUBLIC_RELAY_HTTP="https://$RELAY_FQDN" \
  --build-arg NEXT_PUBLIC_RELAY_WS="wss://$RELAY_FQDN/ws" \
  .

VOICE_LIVE_RESOURCE_ID=$(az cognitiveservices account show \
  --name "$VOICE_LIVE_RESOURCE_NAME" --resource-group "$RESOURCE_GROUP" --query id -o tsv)
VISION_ENDPOINT=$(az cognitiveservices account show \
  --name "$VISION_RESOURCE_NAME" --resource-group "$VISION_RESOURCE_GROUP" --query properties.endpoint -o tsv)
VISION_RESOURCE_ID=$(az cognitiveservices account show \
  --name "$VISION_RESOURCE_NAME" --resource-group "$VISION_RESOURCE_GROUP" --query id -o tsv)

echo "==> Deploying infra via Terraform (Container Apps env, relay + web apps, role assignments)"
cd infra
terraform init -input=false >/dev/null
terraform apply -input=false -auto-approve \
  -var "deploy_cloud_apps=true" \
  -var "relay_image=$ACR_LOGIN_SERVER/idd-relay:$IMAGE_TAG" \
  -var "web_image=$ACR_LOGIN_SERVER/idd-web:$IMAGE_TAG" \
  -var "site_pin=$SITE_PIN" \
  -var "voice_live_resource_group=$RESOURCE_GROUP" \
  -var "voice_live_resource_name=$VOICE_LIVE_RESOURCE_NAME" \
  -var "vision_resource_group=$VISION_RESOURCE_GROUP" \
  -var "vision_resource_name=$VISION_RESOURCE_NAME" \
  -var "vision_deployment=$VISION_DEPLOYMENT"

RELAY_PRINCIPAL_ID=$(terraform output -raw relay_principal_id)
WEB_FQDN=$(terraform output -raw web_fqdn)
RELAY_FQDN=$(terraform output -raw relay_fqdn)
cd ..

echo "==> Granting the relay's managed identity access to the Vision resource (cross-resource-group)"
az role assignment create \
  --assignee-object-id "$RELAY_PRINCIPAL_ID" \
  --assignee-principal-type ServicePrincipal \
  --role "Cognitive Services User" \
  --scope "$VISION_RESOURCE_ID" >/dev/null 2>&1 || true

cat <<EOF

==================================================================
Deployed.

  Web:   https://$WEB_FQDN
  Relay: https://$RELAY_FQDN

Site PIN (share this with visitors, or empty = no gate): $SITE_PIN

Note: managed identity role assignments can take a minute or two to
propagate. If the relay's first live Vision/Voice Live call fails with
403, wait ~60s and retry.
==================================================================
EOF
