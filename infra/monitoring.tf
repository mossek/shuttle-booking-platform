# -----------------------------------------------------------------------------
# Monitoring: Application Insights only (no alerts, to keep costs at zero).
#
# The app sends every request, error and log line here. Look at it any time in
# the portal: rg-shuttle-dev -> appi-shuttle-dev -> Failures / Performance /
# Live metrics / Logs. Nothing emails you - you check it when you want to.
#
# Alerts, the availability test and the action group were removed on purpose:
# the availability test kept waking the app (cost), and the blue-green deploy
# in ci.yml already checks /health/db on every release and rolls back on failure.
# -----------------------------------------------------------------------------

# Workspace-based Application Insights: its data is stored in the SAME Log
# Analytics workspace the container logs already go to - one place to query.
# A small app like this stays inside the free monthly data allowance.
resource "azurerm_application_insights" "main" {
  name                = "appi-${local.name}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  workspace_id        = azurerm_log_analytics_workspace.main.id
  application_type    = "web"
  tags                = local.tags
}
