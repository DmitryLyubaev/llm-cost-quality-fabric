terraform {
  required_version = ">= 1.9"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }
}

# Runs as the personal subscription owner, through the default Azure CLI sign-in (`az login`).
provider "azurerm" {
  features {}

  # azurerm 4 requires the subscription explicitly. It comes from terraform.tfvars, which is
  # git-ignored; it is never committed.
  subscription_id = var.subscription_id
}
