resource "azurerm_resource_group" "event_hubs" {
  name     = "rg-p10-event-hubs"
  location = var.location

  tags = {
    project = "p10"
  }
}

resource "azurerm_eventhub_namespace" "gateway" {
  name                = var.namespace_name
  resource_group_name = azurerm_resource_group.event_hubs.name
  location            = azurerm_resource_group.event_hubs.location
  sku                 = "Standard"
  capacity            = 1

  # Entra-only access: no SAS keys, no connection strings.
  local_authentication_enabled = false
  minimum_tls_version          = "1.2"

  # Public network access stays on, as for the p9 cost-export storage account: the trial
  # capacity cannot reach a private endpoint.
  public_network_access_enabled = true

  tags = {
    project = "p10"
  }
}

# One hub, one partition, one day of retention: enough for a session week and nothing more.
resource "azurerm_eventhub" "gateway_calls" {
  name         = "gateway-calls"
  namespace_id = azurerm_eventhub_namespace.gateway.id

  partition_count = 1

  # One day, in days: the Standard tier allows 1 to 7. (retention_description, in hours, is for
  # the compaction policy and does not apply to a Delete-policy hub.)
  message_retention = 1
}

resource "azurerm_eventhub_consumer_group" "fabric" {
  name                = "fabric"
  namespace_name      = azurerm_eventhub_namespace.gateway.name
  resource_group_name = azurerm_resource_group.event_hubs.name
  eventhub_name       = azurerm_eventhub.gateway_calls.name
}

# The gateway's identity sends; nothing else can.
resource "azurerm_role_assignment" "sender" {
  scope                = azurerm_eventhub.gateway_calls.id
  role_definition_name = "Azure Event Hubs Data Sender"
  principal_id         = var.sender_principal_id
  principal_type       = "ServicePrincipal"
}

# Reads for the Fabric workspace identities, at hub scope.
resource "azurerm_role_assignment" "receiver" {
  for_each             = toset(var.receiver_principal_ids)
  scope                = azurerm_eventhub.gateway_calls.id
  role_definition_name = "Azure Event Hubs Data Receiver"
  principal_id         = each.value
  principal_type       = "ServicePrincipal"
}
