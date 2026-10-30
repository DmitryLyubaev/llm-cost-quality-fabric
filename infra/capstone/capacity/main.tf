# The capstone's paid F2 (phase 4). Apply it at the start of a capstone session and destroy it at
# the end. rg-p12-* is attributed to the `capstone` project in project 9's cost model
# (src/lakehouse/seeds/rg_rules.csv).
resource "azurerm_resource_group" "capstone" {
  name     = "rg-p12-capstone"
  location = var.location

  tags = {
    project = "capstone"
  }
}

# Argument names from the azurerm 4.x docs for azurerm_fabric_capacity: `sku` is a required block
# with `name` (F2 ... F2048) and `tier` (only "Fabric"); `administration_members` takes UPNs for
# users. The name is lower-case letters and digits; infra/capstone/workspace looks the capacity up
# by it.
resource "azurerm_fabric_capacity" "capstone" {
  name                = "capstonef2"
  resource_group_name = azurerm_resource_group.capstone.name
  location            = azurerm_resource_group.capstone.location

  administration_members = [var.capacity_admin_upn]

  sku {
    name = "F2"
    tier = "Fabric"
  }

  tags = {
    project = "capstone"
  }
}
