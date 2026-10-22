variable "owner_object_id" {
  description = "Entra object ID of the owner (user). Becomes Admin on both workspaces. Set in terraform.tfvars."
  type        = string
}

variable "deploy_sp_object_id" {
  description = "Entra object ID of the deploy service principal (the enterprise application, not the app registration). Becomes Contributor on both workspaces. Set in terraform.tfvars."
  type        = string
}
