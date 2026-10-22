# Spec amendment 1 (section 11): the Terraform provider cannot assign a Fabric trial capacity,
# so neither workspace sets capacity_id. The owner assigns the trial capacity once per
# workspace in the Fabric UI after apply.
resource "fabric_workspace" "dev" {
  display_name = "p9-dev"
  description  = "Fabric lakehouse project (p9): dev, connected to the dev branch."

  identity = {
    type = "SystemAssigned"
  }
}

resource "fabric_workspace" "prod" {
  display_name = "p9-prod"
  description  = "Fabric lakehouse project (p9): prod, deployed from main."

  identity = {
    type = "SystemAssigned"
  }
}

resource "fabric_workspace_role_assignment" "owner_dev" {
  workspace_id = fabric_workspace.dev.id
  role         = "Admin"

  principal = {
    id   = var.owner_object_id
    type = "User"
  }
}

resource "fabric_workspace_role_assignment" "owner_prod" {
  workspace_id = fabric_workspace.prod.id
  role         = "Admin"

  principal = {
    id   = var.owner_object_id
    type = "User"
  }
}

resource "fabric_workspace_role_assignment" "deploy_dev" {
  workspace_id = fabric_workspace.dev.id
  role         = "Contributor"

  principal = {
    id   = var.deploy_sp_object_id
    type = "ServicePrincipal"
  }
}

resource "fabric_workspace_role_assignment" "deploy_prod" {
  workspace_id = fabric_workspace.prod.id
  role         = "Contributor"

  principal = {
    id   = var.deploy_sp_object_id
    type = "ServicePrincipal"
  }
}
