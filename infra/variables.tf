# -----------------------------------------------------------------------------
# Inputs. Defaults are set so `terraform apply` works with no extra flags.
# Override any of them with -var="name=value" or a .tfvars file.
# -----------------------------------------------------------------------------

variable "location" {
  description = "Azure region for all resources"
  type        = string
  default     = "southafricanorth"
}

variable "environment" {
  description = "Short environment name, used in resource names (dev, test, prod)"
  type        = string
  default     = "dev"
}

variable "container_image" {
  description = <<-EOT
    Image the Container App starts with on the FIRST deploy.
    It is the public GHCR image the Week 1 pipeline publishes, so the first
    apply works before anything exists in ACR. After that the pipeline
    deploys new versions and Terraform ignores this value (see main.tf).
  EOT
  type        = string
  default     = "ghcr.io/mossek/shuttle-booking-platform:latest"
}

variable "github_deployer_name" {
  description = "Display name of the Entra app registration GitHub Actions uses to deploy"
  type        = string
  default     = "github-shuttle-deploy"
}
variable "website_origins" {
  description = "Websites allowed to call the API from a browser (CORS)"
  type        = list(string)
  default = [
    "https://www.movenmeal.com",
    "https://movenmeal.com",
    "http://localhost:8080", # the website running on your laptop (npm run dev)
  ]
}

variable "notify_email" {
  description = "Microsoft 365 mailbox that receives new quote requests"
  type        = string
  default     = "sales@movenmeal.com"
}

variable "alert_email" {
  description = "Who gets the monitoring alert emails (API down, errors, restarts)"
  type        = string
  default     = "sales@movenmeal.com"
}

variable "health_check_seconds" {
  description = <<-EOT
    How often EACH of the 3 test locations calls /health/db: 300, 600 or 900.
    More often = faster alerts, but the app rarely gets to scale to zero
    (slightly higher Container Apps cost, faster website for customers).
  EOT
  type        = number
  default     = 900

  validation {
    condition     = contains([300, 600, 900], var.health_check_seconds)
    error_message = "health_check_seconds must be 300, 600 or 900."
  }
}
