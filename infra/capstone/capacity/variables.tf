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
  description = "Azure region for the resource group and the F2: the F2's own region, which may differ from the trial capacity's. The australiaeast default is an assumption; confirm the region at the start of the live run."
  type        = string
  default     = "australiaeast"

  validation {
    condition     = length(trimspace(var.location)) > 0
    error_message = "location must not be empty."
  }
}
