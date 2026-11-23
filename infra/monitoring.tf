# -----------------------------------------------------------------------------
# Monitoring and alerts: find out about problems before a customer does.
#
#   Application Insights      the app sends every request, error and log line here
#   Availability test         Azure calls /health/db from 3 cities every few minutes
#   Action group              WHO gets told: an email address
#   Alerts                    WHEN to tell them:
#     1. API or database down     (availability test fails from 2+ cities)
#     2. Server errors            (any HTTP 5xx answer)
#     3. Container restarting     (the app crashed and Azure restarted it)
#     4. Errors in the app's log  (e.g. "Could not email quote request 12")
#
# Portal: Resource group rg-shuttle-dev -> appi-shuttle-dev (Application
# Insights) and Monitor -> Alerts -> Alert rules.
# -----------------------------------------------------------------------------

# Workspace-based Application Insights: its data is stored in the SAME Log
# Analytics workspace the container logs already go to - one place to query.
resource "azurerm_application_insights" "main" {
  name                = "appi-${local.name}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  workspace_id        = azurerm_log_analytics_workspace.main.id
  application_type    = "web"
  tags                = local.tags
}

# WHO gets told. Change the address in terraform.tfvars (alert_email = "...").
resource "azurerm_monitor_action_group" "ops" {
  name                = "ag-${local.name}-ops"
  resource_group_name = azurerm_resource_group.main.name
  short_name          = "shuttleops" # max 12 characters - shown in the email/SMS
  tags                = local.tags

  email_receiver {
    name                    = "ops-email"
    email_address           = var.alert_email
    use_common_alert_schema = true
  }
}

# --- 1. Is the API (and its database) reachable from the internet? -----------
# Azure's test robots call /health/db from 3 cities. /health/db runs
# "SELECT 1" against Postgres, so a stopped database fails the test too.
resource "azurerm_application_insights_standard_web_test" "api_health" {
  name                    = "webtest-${local.name}-api-health"
  resource_group_name     = azurerm_resource_group.main.name
  location                = azurerm_resource_group.main.location
  application_insights_id = azurerm_application_insights.main.id
  description             = "Calls /health/db: API up AND database reachable"
  enabled                 = true
  frequency               = var.health_check_seconds # how often EACH city checks
  timeout                 = 60                       # allow for a cold start from zero
  retry_enabled           = true                     # one retry before counting a failure

  # Test locations (Azure has none in South Africa). These are
  # West Europe (Amsterdam), North Europe (Dublin) and France Central (Paris).
  geo_locations = ["emea-nl-ams-azr", "emea-gb-db3-azr", "emea-fr-pra-edge"]

  request {
    url = "https://${azurerm_container_app.api.ingress[0].fqdn}/health/db"
  }

  validation_rules {
    expected_status_code        = 200
    ssl_check_enabled           = true
    ssl_cert_remaining_lifetime = 7 # also warn if the HTTPS certificate is about to expire
  }

  # Azure links a web test to its Application Insights with this tag. Setting it
  # ourselves stops `terraform plan` from showing a change every time.
  tags = merge(local.tags, {
    "hidden-link:${azurerm_application_insights.main.id}" = "Resource"
  })
}

resource "azurerm_monitor_metric_alert" "api_down" {
  name                = "alert-${local.name}-api-down"
  resource_group_name = azurerm_resource_group.main.name
  scopes = [
    azurerm_application_insights_standard_web_test.api_health.id,
    azurerm_application_insights.main.id,
  ]
  description = "The API or its database is unreachable from 2 or more test locations."
  severity    = 1 # 0 = critical ... 4 = verbose
  frequency   = "PT5M"
  window_size = "PT15M"
  tags        = local.tags

  application_insights_web_test_location_availability_criteria {
    web_test_id           = azurerm_application_insights_standard_web_test.api_health.id
    component_id          = azurerm_application_insights.main.id
    failed_location_count = 2 # 1 city failing is often that city's network, not us
  }

  action {
    action_group_id = azurerm_monitor_action_group.ops.id
  }
}

# --- 2. Is the API answering with server errors? -----------------------------
# 5xx = OUR fault (a crash, the database refused). 4xx are the customer's
# (wrong phone number, missing field) and are expected, so they don't alert.
resource "azurerm_monitor_metric_alert" "server_errors" {
  name                = "alert-${local.name}-server-errors"
  resource_group_name = azurerm_resource_group.main.name
  scopes              = [azurerm_container_app.api.id]
  description         = "The API answered at least one request with an HTTP 5xx error."
  severity            = 2
  frequency           = "PT5M"
  window_size         = "PT15M"
  tags                = local.tags

  criteria {
    metric_namespace = "Microsoft.App/containerApps"
    metric_name      = "Requests"
    aggregation      = "Total"
    operator         = "GreaterThan"
    threshold        = 0

    dimension {
      name     = "statusCodeCategory"
      operator = "Include"
      values   = ["5xx"]
    }
  }

  action {
    action_group_id = azurerm_monitor_action_group.ops.id
  }
}

# --- 3. Is the container crashing? -------------------------------------------
# A restart means the app died (e.g. a migration failed at startup) or failed
# its liveness probe, and Azure started it again.
resource "azurerm_monitor_metric_alert" "restarts" {
  name                = "alert-${local.name}-restarts"
  resource_group_name = azurerm_resource_group.main.name
  scopes              = [azurerm_container_app.api.id]
  description         = "The API container restarted (crash or failed liveness probe)."
  severity            = 2
  frequency           = "PT5M"
  window_size         = "PT15M"
  tags                = local.tags

  criteria {
    metric_namespace = "Microsoft.App/containerApps"
    metric_name      = "RestartCount"
    aggregation      = "Total"
    operator         = "GreaterThan"
    threshold        = 0
  }

  action {
    action_group_id = azurerm_monitor_action_group.ops.id
  }
}

# --- 4. Did the app log an error? --------------------------------------------
# A log search (KQL) over Application Insights. Catches problems that still
# return 200 to the customer - like "Could not email quote request 12", where
# the request was saved but the email to sales@ failed.
resource "azurerm_monitor_scheduled_query_rules_alert_v2" "app_errors" {
  name                 = "alert-${local.name}-app-errors"
  resource_group_name  = azurerm_resource_group.main.name
  location             = azurerm_resource_group.main.location
  scopes               = [azurerm_application_insights.main.id]
  description          = "The API logged an error or exception (for example a failed email)."
  severity             = 2
  evaluation_frequency = "PT15M"
  window_duration      = "PT15M"
  tags                 = local.tags

  criteria {
    query                   = <<-KQL
      union
        (traces | where severityLevel >= 3),
        (exceptions)
    KQL
    time_aggregation_method = "Count"
    operator                = "GreaterThan"
    threshold               = 0

    failing_periods {
      minimum_failing_periods_to_trigger_alert = 1
      number_of_evaluation_periods             = 1
    }
  }

  action {
    action_groups = [azurerm_monitor_action_group.ops.id]
  }
}
