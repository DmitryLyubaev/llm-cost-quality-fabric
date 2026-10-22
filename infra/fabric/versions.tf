terraform {
  required_version = ">= 1.9"

  required_providers {
    fabric = {
      source  = "microsoft/fabric"
      version = "~> 1.14"
    }
  }
}

# Azure CLI authentication in the signed-in user's context (the provider default).
# No credentials appear in code: run `az login` as the personal-tenant user first.
provider "fabric" {}
