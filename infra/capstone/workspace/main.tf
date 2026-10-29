# The F2 made by infra/capstone/capacity, found by its name (the Fabric capacity ID is not the Azure
# resource ID, and neither is committed). fabric@ administers it, so it can see and assign it.
data "fabric_capacity" "capstone" {
  display_name = "capstonef2"
}

# fabric@ creates the workspace and so is already Admin on it. Every live capstone step runs as
# fabric@, so no role assignment is needed.
resource "fabric_workspace" "capstone" {
  display_name = "capstone"
  description  = "Phase 4 capstone: the data agent and its snapshot lakehouse."
  capacity_id  = data.fabric_capacity.capstone.id
}
