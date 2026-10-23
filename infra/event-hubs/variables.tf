variable "subscription_id" {
  description = "Azure subscription ID. Set in terraform.tfvars; never committed."
  type        = string
  sensitive   = true
}

variable "namespace_name" {
  description = "Globally unique Event Hubs namespace name (6 to 50 letters, digits and hyphens). Set in terraform.tfvars; never committed."
  type        = string
  sensitive   = true
}

variable "location" {
  description = "Azure region for the resource group and the namespace."
  type        = string
  default     = "australiaeast"
}

variable "sender_principal_id" {
  description = "Object ID of the ReleaseLens gateway's user-assigned identity, granted Azure Event Hubs Data Sender on the hub. Set in terraform.tfvars; never committed."
  type        = string
  sensitive   = true
}

variable "receiver_principal_ids" {
  description = "Object IDs granted Azure Event Hubs Data Receiver on the hub: the Fabric workspace identities from infra/fabric."
  type        = list(string)
  default     = []
}
