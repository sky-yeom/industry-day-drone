#!/usr/bin/env bash
# Deploys the relay + web dashboard to Azure Container Apps, wired to the
# existing Azure AI Foundry Voice Live resource and an Azure Vision
# deployment. Requires: az CLI, logged in (`az login`), Contributor +
# User Access Administrator on the target resource group (for the role
# assignment this script creates on the Vision resource).
#
# Usage:
#   ./scripts/deploy.sh
#
# Override any of these via environment variables before running:
#   RESOURCE_GROUP, LOCATION, ACR_NAME, SITE_PIN,
#   VISION_RESOURCE_GROUP, VISION_RESOURCE_NAME, VISION_DEPLOYMENT
set -euo pipefail

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

echo "==> Building + pushing relay image (az acr build, no local Docker needed)"
az acr build --registry "$ACR_NAME" --image "idd-relay:$IMAGE_TAG" --file relay/Dockerfile .

echo "==> Building + pushing web image"
az acr build --registry "$ACR_NAME" --image "idd-web:$IMAGE_TAG" --file Dockerfile .

VOICE_LIVE_RESOURCE_ID=$(az cognitiveservices account show \
  --name "$VOICE_LIVE_RESOURCE_NAME" --resource-group "$RESOURCE_GROUP" --query id -o tsv)
VISION_ENDPOINT=$(az cognitiveservices account show \
  --name "$VISION_RESOURCE_NAME" --resource-group "$VISION_RESOURCE_GROUP" --query properties.endpoint -o tsv)
VISION_RESOURCE_ID=$(az cognitiveservices account show \
  --name "$VISION_RESOURCE_NAME" --resource-group "$VISION_RESOURCE_GROUP" --query id -o tsv)

echo "==> Deploying infra (Container Apps env, relay + web apps, Voice Live role assignment)"
DEPLOY_OUTPUT=$(az deployment group create \
  --resource-group "$RESOURCE_GROUP" \
  --template-file infra/main.bicep \
  --parameters \
    location="$LOCATION" \
    acrName="$ACR_NAME" \
    relayImage="$ACR_LOGIN_SERVER/idd-relay:$IMAGE_TAG" \
    webImage="$ACR_LOGIN_SERVER/idd-web:$IMAGE_TAG" \
    voiceLiveResourceId="$VOICE_LIVE_RESOURCE_ID" \
    voiceLiveResourceName="$VOICE_LIVE_RESOURCE_NAME" \
    voiceLiveRegion="$VOICE_LIVE_REGION" \
    visionEndpoint="$VISION_ENDPOINT" \
    visionDeployment="$VISION_DEPLOYMENT" \
    sitePin="$SITE_PIN" \
  --query "properties.outputs" -o json)

RELAY_PRINCIPAL_ID=$(echo "$DEPLOY_OUTPUT" | python3 -c "import json,sys; print(json.load(sys.stdin)['relayPrincipalId']['value'])")
WEB_FQDN=$(echo "$DEPLOY_OUTPUT" | python3 -c "import json,sys; print(json.load(sys.stdin)['webFqdn']['value'])")
RELAY_FQDN=$(echo "$DEPLOY_OUTPUT" | python3 -c "import json,sys; print(json.load(sys.stdin)['relayFqdn']['value'])")

echo "==> Granting the relay's managed identity access to the Vision resource (cross-resource-group)"
az role assignment create \
  --assignee-object-id "$RELAY_PRINCIPAL_ID" \
  --assignee-principal-type ServicePrincipal \
  --role "Cognitive Services User" \
  --scope "$VISION_RESOURCE_ID" >/dev/null

cat <<EOF

==================================================================
Deployed.

  Web:   https://$WEB_FQDN
  Relay: https://$RELAY_FQDN

Site PIN (share this with visitors): $SITE_PIN

Note: managed identity role assignments can take a minute or two to
propagate. If the relay's first live Vision/Voice Live call fails with
403, wait ~60s and retry.
==================================================================
EOF
