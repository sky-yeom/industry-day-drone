# Preserves shared infrastructure; the retired public demo apps (relay + web)
# and their exclusively owned role assignments require deploy_cloud_apps=true.
# Uses the existing `industry-day-drone` resource group and Log Analytics workspace.
# The Voice Live and Vision Cognitive Services accounts already exist
# (managed elsewhere / by another team) and are only read here, never
# created or destroyed by this config — they're looked up as `data` sources.
# The relay's system-assigned managed identity is granted "Cognitive
# Services User" on both, even though they can live in different resource
# groups (and, in principle, different subscriptions).

data "azurerm_resource_group" "main" {
  name = var.resource_group_name
}

data "azurerm_log_analytics_workspace" "logs" {
  name                = var.log_analytics_workspace_name
  resource_group_name = data.azurerm_resource_group.main.name
}

data "azurerm_cognitive_account" "voice_live" {
  count = var.deploy_cloud_apps ? 1 : 0

  name                = var.voice_live_resource_name
  resource_group_name = var.voice_live_resource_group
}

data "azurerm_cognitive_account" "vision" {
  count = var.deploy_cloud_apps ? 1 : 0

  name                = var.vision_resource_name
  resource_group_name = var.vision_resource_group
}

resource "azurerm_container_registry" "acr" {
  name                = var.acr_name
  resource_group_name = data.azurerm_resource_group.main.name
  location            = var.location
  sku                 = "Basic"
  admin_enabled       = true
}

resource "azurerm_container_app_environment" "main" {
  name                       = var.container_apps_env_name
  location                   = var.location
  resource_group_name        = data.azurerm_resource_group.main.name
  log_analytics_workspace_id = data.azurerm_log_analytics_workspace.logs.id
}

resource "azurerm_container_app" "relay" {
  count = var.deploy_cloud_apps ? 1 : 0

  name                         = var.relay_app_name
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = data.azurerm_resource_group.main.name
  revision_mode                = "Single"

  identity {
    type = "SystemAssigned"
  }

  registry {
    server               = azurerm_container_registry.acr.login_server
    username             = azurerm_container_registry.acr.admin_username
    password_secret_name = "acr-password"
  }

  secret {
    name  = "acr-password"
    value = azurerm_container_registry.acr.admin_password
  }

  ingress {
    external_enabled = true
    target_port      = 8080
    transport        = "auto"
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = 0
    max_replicas = 3

    container {
      name   = "relay"
      image  = var.relay_image
      cpu    = 0.5
      memory = "1Gi"

      env {
        name  = "TRIAGE_MODE"
        value = var.triage_mode
      }
      env {
        name  = "VOICE_LIVE_RESOURCE"
        value = var.voice_live_resource_name
      }
      env {
        name  = "VOICE_LIVE_REGION"
        value = var.voice_live_region
      }
      env {
        name  = "AZURE_VISION_ENDPOINT"
        value = data.azurerm_cognitive_account.vision[0].endpoint
      }
      env {
        name  = "AZURE_VISION_DEPLOYMENT"
        value = var.vision_deployment
      }
      env {
        name  = "RELAY_WEB_ORIGIN_REGEX"
        value = "https://.*\\.azurecontainerapps\\.io"
      }
    }
  }

  lifecycle {
    # Explicitly opted-in manual CI runs deploy new images imperatively via
    # `az containerapp update`; ignore drift here so
    # a later `terraform apply` doesn't revert to the tfvars image tag.
    ignore_changes = [template[0].container[0].image]
  }
}

resource "azurerm_role_assignment" "relay_voice_live" {
  count = var.deploy_cloud_apps ? 1 : 0

  # Scoped at the resource group (not just the cognitive account) to match
  # what was actually provisioned; the Voice Live account and this demo
  # stack share the same resource group anyway.
  scope                = data.azurerm_resource_group.main.id
  role_definition_name = "Cognitive Services User"
  principal_id         = azurerm_container_app.relay[0].identity[0].principal_id
}

resource "azurerm_role_assignment" "relay_vision" {
  count = var.deploy_cloud_apps ? 1 : 0

  scope                = data.azurerm_cognitive_account.vision[0].id
  role_definition_name = "Cognitive Services User"
  principal_id         = azurerm_container_app.relay[0].identity[0].principal_id
}

resource "azurerm_container_app" "web" {
  count = var.deploy_cloud_apps ? 1 : 0

  name                         = var.web_app_name
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = data.azurerm_resource_group.main.name
  revision_mode                = "Single"

  registry {
    server               = azurerm_container_registry.acr.login_server
    username             = azurerm_container_registry.acr.admin_username
    password_secret_name = "acr-password"
  }

  secret {
    name  = "acr-password"
    value = azurerm_container_registry.acr.admin_password
  }

  dynamic "secret" {
    # Container Apps rejects a secret with an empty string value, so the
    # site-pin secret (and its SITE_ACCESS_PIN env var below) are only
    # created at all when a PIN is actually configured — leaving
    # site_pin empty disables the gate entirely.
    for_each = local.site_pin_enabled ? ["site-pin"] : []
    content {
      name  = "site-pin"
      value = var.site_pin
    }
  }

  ingress {
    external_enabled = true
    target_port      = 3000
    transport        = "auto"
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = 0
    max_replicas = 3

    container {
      name   = "web"
      image  = var.web_image
      cpu    = 0.5
      memory = "1Gi"

      env {
        name  = "NEXT_PUBLIC_RELAY_HTTP"
        value = "https://${azurerm_container_app.relay[0].ingress[0].fqdn}"
      }
      env {
        name  = "NEXT_PUBLIC_RELAY_WS"
        value = "wss://${azurerm_container_app.relay[0].ingress[0].fqdn}/ws"
      }

      dynamic "env" {
        for_each = local.site_pin_enabled ? ["site-pin"] : []
        content {
          name        = "SITE_ACCESS_PIN"
          secret_name = "site-pin"
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].container[0].image]
  }
}

moved {
  from = azurerm_container_app.relay
  to   = azurerm_container_app.relay[0]
}

moved {
  from = azurerm_container_app.web
  to   = azurerm_container_app.web[0]
}

moved {
  from = azurerm_role_assignment.relay_voice_live
  to   = azurerm_role_assignment.relay_voice_live[0]
}

moved {
  from = azurerm_role_assignment.relay_vision
  to   = azurerm_role_assignment.relay_vision[0]
}

moved {
  from = data.azurerm_cognitive_account.voice_live
  to   = data.azurerm_cognitive_account.voice_live[0]
}

moved {
  from = data.azurerm_cognitive_account.vision
  to   = data.azurerm_cognitive_account.vision[0]
}
