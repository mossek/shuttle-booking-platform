# -----------------------------------------------------------------------------
# Permissions for the GitHub Actions deployment identity.
#
# The identity itself (app registration + federated credential) is NOT managed
# here on purpose: it must outlive this environment. Terraform only READS it,
# and manages the role assignments, which belong to this environment.
# -----------------------------------------------------------------------------

# Read the existing service principal (Entra ID -> Enterprise applications)
data "azuread_service_principal" "github_deployer" {
  display_name = var.github_deployer_name
}

# The pipeline may push images to THIS registry only
resource "azurerm_role_assignment" "github_acr_push" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPush"
  principal_id         = data.azuread_service_principal.github_deployer.object_id
  principal_type       = "ServicePrincipal"
}

# The pipeline may update resources in THIS resource group only
# (Contributor cannot grant access, so it cannot escalate its own permissions)
resource "azurerm_role_assignment" "github_rg_contributor" {
  scope                = azurerm_resource_group.main.id
  role_definition_name = "Contributor"
  principal_id         = data.azuread_service_principal.github_deployer.object_id
  principal_type       = "ServicePrincipal"
}