# Deploys the public demo stack (relay + web) into the existing
# `industry-day-drone` resource group, reusing its Log Analytics workspace.
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
  name                = var.voice_live_resource_name
  resource_group_name = var.voice_live_resource_group
}

data "azurerm_cognitive_account" "vision" {
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
        value = data.azurerm_cognitive_account.vision.endpoint
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
}

resource "azurerm_role_assignment" "relay_voice_live" {
  # Scoped at the resource group (not just the cognitive account) to match
  # what was actually provisioned; the Voice Live account and this demo
  # stack share the same resource group anyway.
  scope                = data.azurerm_resource_group.main.id
  role_definition_name = "Cognitive Services User"
  principal_id         = azurerm_container_app.relay.identity[0].principal_id
}

resource "azurerm_role_assignment" "relay_vision" {
  scope                = data.azurerm_cognitive_account.vision.id
  role_definition_name = "Cognitive Services User"
  principal_id         = azurerm_container_app.relay.identity[0].principal_id
}

resource "azurerm_container_app" "web" {
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
        value = "https://${azurerm_container_app.relay.ingress[0].fqdn}"
      }
      env {
        name  = "NEXT_PUBLIC_RELAY_WS"
        value = "wss://${azurerm_container_app.relay.ingress[0].fqdn}/ws"
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
}
