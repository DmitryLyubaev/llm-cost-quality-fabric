terraform {
  required_version = ">= 1.9"

  required_providers {
    fabric = {
      source  = "microsoft/fabric"
      version = "~> 1.14"
    }
  }
}

# Runs as fabric@, through Azure CLI signed in under its own config directory:
# `export AZURE_CONFIG_DIR=$HOME/.azure-fabric`, then `az login`. No credentials appear in code.
provider "fabric" {}
