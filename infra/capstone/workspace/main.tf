# The F2 made by infra/capstone/capacity, found by its name (the Fabric capacity ID is not the Azure
# resource ID, and neither is committed). fabric@ administers it, so it can see and assign it.
# Setting var.capacity_id skips the lookup and uses that ID instead. Either way the capacity must be
# an active F2: the postcondition is checked on the lookup (the data source exposes sku and state).
data "fabric_capacity" "capstone" {
  count        = var.capacity_id == null ? 1 : 0
  display_name = "capstonef2"

  lifecycle {
    postcondition {
      condition     = self.sku == "F2" && self.state == "Active"
      error_message = "The capacity capstonef2 must be an active F2 (apply infra/capstone/capacity first, and resume it if it is paused)."
    }
  }
}

# fabric@ creates the workspace and so is already Admin on it. Every live capstone step runs as
# fabric@, so no role assignment is needed.
resource "fabric_workspace" "capstone" {
  display_name = "capstone"
  description  = "Phase 4 capstone: the data agent and its snapshot lakehouse."
  capacity_id  = coalesce(var.capacity_id, one(data.fabric_capacity.capstone[*].id))
}
