resource "azurerm_resource_group" "cost_export" {
  name     = "rg-p9-cost-export"
  location = var.location

  tags = {
    project = "p9"
  }
}

resource "azurerm_storage_account" "cost_export" {
  name                     = var.storage_account_name
  resource_group_name      = azurerm_resource_group.cost_export.name
  location                 = azurerm_resource_group.cost_export.location
  account_kind             = "StorageV2"
  account_tier             = "Standard"
  account_replication_type = "LRS"

  # Hierarchical namespace: ADLS Gen2, which the Fabric shortcut reads.
  is_hns_enabled = true

  # Entra-only access: no account keys, no SAS signed by a key.
  shared_access_key_enabled       = false
  allow_nested_items_to_be_public = false
  min_tls_version                 = "TLS1_2"

  # Spec amendment 6 (section 11): public network access stays on because the trial cannot use
  # trusted workspace access to a firewalled account. The owner decides this at the storage
  # step of plan 3; if the owner refuses, plan 3 stops there.
  public_network_access_enabled = true

  tags = {
    project = "p9"
  }
}

resource "azurerm_storage_container" "cost_exports" {
  name                  = "cost-exports"
  storage_account_id    = azurerm_storage_account.cost_export.id
  container_access_type = "private"
}

# Reads for the workspace identities, at account scope.
resource "azurerm_role_assignment" "reader" {
  for_each             = toset(var.reader_principal_ids)
  scope                = azurerm_storage_account.cost_export.id
  role_definition_name = "Storage Blob Data Reader"
  principal_id         = each.value
}
