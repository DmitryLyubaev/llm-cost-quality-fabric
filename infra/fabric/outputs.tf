# Workspace IDs go into the deploy configuration. The identities' service principal IDs are
# the Entra object IDs of the workspace identities; infra/cost-export takes them as
# reader_principal_ids. Neither is a secret.
output "dev_workspace_id" {
  description = "ID of the p9-dev workspace."
  value       = fabric_workspace.dev.id
}

output "prod_workspace_id" {
  description = "ID of the p9-prod workspace."
  value       = fabric_workspace.prod.id
}

output "dev_workspace_identity_service_principal_id" {
  description = "Object ID of the p9-dev workspace identity (reader for infra/cost-export)."
  value       = fabric_workspace.dev.identity.service_principal_id
}

output "prod_workspace_identity_service_principal_id" {
  description = "Object ID of the p9-prod workspace identity (reader for infra/cost-export)."
  value       = fabric_workspace.prod.identity.service_principal_id
}
