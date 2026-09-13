output "acr_login_server" {
  value = azurerm_container_registry.acr.login_server
}

output "relay_fqdn" {
  value = var.deploy_cloud_apps ? azurerm_container_app.relay[0].ingress[0].fqdn : null
}

output "web_fqdn" {
  value = var.deploy_cloud_apps ? azurerm_container_app.web[0].ingress[0].fqdn : null
}

output "relay_principal_id" {
  value = var.deploy_cloud_apps ? azurerm_container_app.relay[0].identity[0].principal_id : null
}
