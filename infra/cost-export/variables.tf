variable "subscription_id" {
  description = "Azure subscription ID. Set in terraform.tfvars; never committed."
  type        = string
  sensitive   = true
}

variable "storage_account_name" {
  description = "Globally unique storage account name (3 to 24 lower-case letters and digits). Set in terraform.tfvars; never committed."
  type        = string
  sensitive   = true
}

variable "location" {
  description = "Azure region for the resource group and the storage account."
  type        = string
  default     = "australiaeast"
}

variable "reader_principal_ids" {
  description = "Object IDs granted Storage Blob Data Reader on the account: the two workspace identities from infra/fabric."
  type        = list(string)
  default     = []
}
