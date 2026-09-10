output "acr_login_server" {
  value = azurerm_container_registry.acr.login_server
}

output "relay_fqdn" {
  value = azurerm_container_app.relay.ingress[0].fqdn
}

output "web_fqdn" {
  value = azurerm_container_app.web.ingress[0].fqdn
}

output "relay_principal_id" {
  value = azurerm_container_app.relay.identity[0].principal_id
}
