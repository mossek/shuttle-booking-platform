# -----------------------------------------------------------------------------
# Which Terraform / provider versions this code is written for,
# and WHERE Terraform keeps its state file.
# -----------------------------------------------------------------------------
terraform {
  required_version = ">= 1.9"

  required_providers {
    # The provider is the plugin that knows how to talk to Azure's API.
    # Pinned to the 4.x line: "~> 4.81" = any 4.x from 4.81 up, never 5.0
    # (a major version can contain breaking changes).
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.81"
    }
    # Generates a random suffix so globally-unique names (like ACR) don't clash.
    random = {
      source  = "hashicorp/random"
      version = "~> 3.9"
    }
  }

  # Remote state: the storage account you created by hand in Step 2.
  backend "azurerm" {
    resource_group_name  = "rg-shuttle-tfstate"
    storage_account_name = "stshuttletf43202"
    container_name       = "tfstate"
    key                  = "shuttle-dev.tfstate" # the blob (file) name inside the container
    use_azuread_auth     = true                  # sign in with Entra ID, not a storage key
  }
}

provider "azurerm" {
  features {}
  # No subscription_id here on purpose: it is read from the
  # ARM_SUBSCRIPTION_ID environment variable, so no IDs are committed to Git
  # and the pipeline can set its own.
}
