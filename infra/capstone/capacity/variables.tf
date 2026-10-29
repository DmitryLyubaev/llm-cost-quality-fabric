variable "subscription_id" {
  description = "Azure subscription ID. Set in terraform.tfvars; never committed."
  type        = string
  sensitive   = true
}

variable "capacity_admin_upn" {
  description = "UPN of the fabric@ user, who administers the F2 and so can assign it to the workspace. Set in terraform.tfvars; never committed."
  type        = string
  sensitive   = true
}

variable "location" {
  description = "Azure region for the resource group and the F2 (the trial capacity's region)."
  type        = string
  default     = "australiaeast"
}
