# -----------------------------------------------------------------------------
# Values printed after `terraform apply` (and readable with `terraform output`)
# -----------------------------------------------------------------------------

output "app_url" {
  description = "Public URL of the API"
  value       = "https://${azurerm_container_app.api.ingress[0].fqdn}"
}

output "resource_group" {
  value = azurerm_resource_group.main.name
}

output "acr_login_server" {
  description = "Registry address the pipeline will push to"
  value       = azurerm_container_registry.main.login_server
}

output "container_app_name" {
  value = azurerm_container_app.api.name
}

output "postgres_host" {
  description = "Hostname the app connects to"
  value       = azurerm_postgresql_flexible_server.main.fqdn
}

output "operator_api_key" {
  description = "Paste into /docs -> Authorize. Read with: terraform output -raw operator_api_key"
  value       = random_password.operator_api_key.result
  sensitive   = true
}
