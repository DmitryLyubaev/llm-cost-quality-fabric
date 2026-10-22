# Infrastructure

Two independent Terraform stacks. Each uses local state (`terraform.tfstate`), which is
git-ignored along with `*.tfvars`, `.terraform/` and plan files. The provider lock files
(`.terraform.lock.hcl`) are committed so provider hashes stay pinned. CI runs `terraform fmt
-check`, `init -backend=false` and `validate` only; nothing runs `plan` or `apply` in CI.

## `fabric/`

Creates the two Fabric workspaces `p9-dev` and `p9-prod`, each with a system-assigned workspace
identity, and their role assignments: the owner as Admin and the deploy service principal as
Contributor on both. It sets no capacity, because the provider cannot assign a trial capacity
(spec amendment 1); assign the trial capacity in the Fabric UI after apply. Values from
`terraform.tfvars`: `owner_object_id` and `deploy_sp_object_id`. Outputs the two workspace IDs
and the two workspace identities' service principal IDs. Authenticates through Azure CLI as the
signed-in user (`az login`).

## `cost-export/`

Creates resource group `rg-p9-cost-export` and a StorageV2 account with hierarchical namespace,
shared keys off, TLS 1.2 and no public blob access, plus the container `cost-exports`. Public
network access is on (spec amendment 6; the owner decides at the storage step). It grants
Storage Blob Data Reader at account scope to each principal in `reader_principal_ids`, which
takes the two workspace identity IDs from `fabric/`. Values from `terraform.tfvars`:
`subscription_id`, `storage_account_name` and `reader_principal_ids`; `location` defaults to
`australiaeast`. It has no outputs, so the account name never prints.
