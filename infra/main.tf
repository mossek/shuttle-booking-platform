# -----------------------------------------------------------------------------
# The infrastructure for the Shuttle Booking API.
#
#   Resource group
#   ├── Log Analytics workspace     (where container logs go)
#   ├── Container Registry (ACR)    (private image registry)
#   ├── User-assigned identity      (the app's identity, allowed to pull from ACR)
#   ├── Container Apps environment  (the managed "cluster" the app runs in)
#   └── Container App               (the API itself, public HTTPS endpoint)
# -----------------------------------------------------------------------------

locals {
  name = "shuttle-${var.environment}" # e.g. shuttle-dev

  # Tags on every resource: who owns it and how it was made
  tags = {
    project     = "shuttle-booking-platform"
    environment = var.environment
    managed_by  = "terraform"
  }
}

# Random 5-character suffix for names that must be unique across all of Azure
resource "random_string" "suffix" {
  length  = 5
  upper   = false
  special = false
}

resource "azurerm_resource_group" "main" {
  name     = "rg-${local.name}"
  location = var.location
  tags     = local.tags
}

# --- Logging -----------------------------------------------------------------
resource "azurerm_log_analytics_workspace" "main" {
  name                = "log-${local.name}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  sku                 = "PerGB2018" # pay per GB ingested - tiny for this app
  retention_in_days   = 30          # the minimum, keeps cost down
  tags                = local.tags
}

# --- Container registry ------------------------------------------------------
resource "azurerm_container_registry" "main" {
  # Only letters and numbers allowed, globally unique: e.g. acrshuttledevx7k2p
  name                = "acrshuttle${var.environment}${random_string.suffix.result}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  sku                 = "Basic"
  admin_enabled       = false # no shared admin password - identities only
  tags                = local.tags
}

# --- Identity the app uses to pull images ------------------------------------
resource "azurerm_user_assigned_identity" "app" {
  name                = "id-${local.name}-api"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  tags                = local.tags
}

# Least privilege: the app may only PULL from this one registry
resource "azurerm_role_assignment" "app_acr_pull" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}

# --- Where the app runs ------------------------------------------------------
resource "azurerm_container_app_environment" "main" {
  name                       = "cae-${local.name}"
  location                   = azurerm_resource_group.main.location
  resource_group_name        = azurerm_resource_group.main.name
  logs_destination           = "log-analytics"
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
  tags                       = local.tags
}

# --- The API -----------------------------------------------------------------
resource "azurerm_container_app" "api" {
  name                         = "ca-${local.name}-api"
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main.id
  revision_mode                = "Single" # one live version at a time
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.app.id]
  }

  # Pull from ACR using the identity above - no username/password stored
  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.app.id
  }

  ingress {
    external_enabled = true # public HTTPS URL
    target_port      = 8000 # the port uvicorn listens on inside the container

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = 0 # scale to zero when idle = almost no cost
    max_replicas = 1

    container {
      name   = "api"
      image  = var.container_image
      cpu    = 0.25
      memory = "0.5Gi" # Container Apps requires memory = 2 x cpu at this size

      # Same /health endpoint your Dockerfile HEALTHCHECK uses
      liveness_probe {
        transport = "HTTP"
        port      = 8000
        path      = "/health"
      }
    }
  }

  # After the first deploy, the PIPELINE decides which image runs.
  # Without this, every `terraform apply` would roll the app back to
  # var.container_image.
  lifecycle {
    ignore_changes = [template[0].container[0].image]
  }

  # Make sure the pull permission exists before the app tries to use it
  depends_on = [azurerm_role_assignment.app_acr_pull]
}
