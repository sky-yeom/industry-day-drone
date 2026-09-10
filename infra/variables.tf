variable "resource_group_name" {
  description = "Existing resource group to deploy the demo stack into."
  type        = string
  default     = "industry-day-drone"
}

variable "location" {
  description = "Azure region for the new resources (Container Apps, ACR)."
  type        = string
  default     = "southeastasia"
}

variable "log_analytics_workspace_name" {
  description = "Existing Log Analytics workspace (created alongside the Foundry resource) to send Container Apps logs to."
  type        = string
  default     = "industry-day-drone-boot-resource-logs"
}

variable "acr_name" {
  description = "Globally-unique Azure Container Registry name."
  type        = string
  default     = "industrydaydrone"
}

variable "container_apps_env_name" {
  description = "Container Apps Environment name."
  type        = string
  default     = "industry-day-drone-env"
}

variable "relay_app_name" {
  description = "Relay (Python/WebSocket) container app name."
  type        = string
  default     = "idd-relay"
}

variable "web_app_name" {
  description = "Web (Next.js) container app name."
  type        = string
  default     = "idd-web"
}

variable "relay_image" {
  description = "Full image reference for the relay, e.g. myacr.azurecr.io/idd-relay:20260101000000. Build/push it first with `az acr build` (see scripts/deploy.sh)."
  type        = string
}

variable "web_image" {
  description = "Full image reference for the web app, e.g. myacr.azurecr.io/idd-web:20260101000000."
  type        = string
}

variable "voice_live_resource_group" {
  description = "Resource group of the existing Azure AI Foundry / Voice Live account."
  type        = string
  default     = "industry-day-drone"
}

variable "voice_live_resource_name" {
  description = "Voice Live resource name (also used as the VOICE_LIVE_RESOURCE env var)."
  type        = string
  default     = "industry-day-drone-boot-resource"
}

variable "voice_live_region" {
  description = "Voice Live region (used as the VOICE_LIVE_REGION env var)."
  type        = string
  default     = "southeastasia"
}

variable "vision_resource_group" {
  description = "Resource group of the existing Azure Vision account. Can differ from resource_group_name."
  type        = string
  default     = "dev-rg-personal-eastus"
}

variable "vision_resource_name" {
  description = "Azure Vision Cognitive Services account name."
  type        = string
  default     = "image-detection-testing-resource"
}

variable "vision_deployment" {
  description = "Azure Vision deployment name (a multimodal chat-completions deployment that supports image input + structured outputs)."
  type        = string
  default     = "gpt-5"
}

variable "triage_mode" {
  description = "TRIAGE_MODE for the relay: \"mock\" (no Azure calls) or \"azure\" (live)."
  type        = string
  default     = "azure"
  validation {
    condition     = contains(["mock", "azure"], var.triage_mode)
    error_message = "triage_mode must be \"mock\" or \"azure\"."
  }
}

variable "site_pin" {
  description = "Shared PIN required to reach the public site. Each session drives billed Voice Live + Vision calls — leave empty only if you intentionally want the link open to anyone."
  type        = string
  sensitive   = true
  default     = ""
}

locals {
  # Terraform won't allow a sensitive value to drive for_each directly;
  # this only leaks whether a PIN is set, not the PIN itself.
  site_pin_enabled = nonsensitive(var.site_pin != "")
}
