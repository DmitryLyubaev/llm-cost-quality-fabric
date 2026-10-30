variable "capacity_id" {
  description = "Optional Fabric capacity ID (the GUID, not the Azure resource ID) that overrides the lookup of the F2 by name. Leave unset to look up capstonef2; set it in terraform.tfvars if that lookup fails. Never committed."
  type        = string
  default     = null

  validation {
    condition     = var.capacity_id == null || length(trimspace(var.capacity_id)) > 0
    error_message = "capacity_id must be unset or a non-empty Fabric capacity ID."
  }
}
