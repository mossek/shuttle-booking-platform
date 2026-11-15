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
