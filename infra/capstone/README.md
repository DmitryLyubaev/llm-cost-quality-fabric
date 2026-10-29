# Capstone F2 and workspace

The phase 4 capstone runs on a paid **F2** Fabric capacity in its own workspace `capstone`. Apply
both stacks at the start of a capstone session and destroy them at the end. The capacity's spend is
attributed to the project `capstone` in project 9's cost model: `rg-p12-*` maps to `capstone` in
`src/lakehouse/seeds/rg_rules.csv`.

## Two stacks, two sign-ins

One apply cannot sign in as both identities. The Azure part runs as the personal subscription
owner and the Fabric part runs as `fabric@`, and both providers read the Azure CLI's config
directory from the process environment (`AZURE_CONFIG_DIR`), which has no per-provider setting.
So there are two stacks:

| Stack | Provider | Signed in as | Creates |
|---|---|---|---|
| `capacity/` | `hashicorp/azurerm` `~> 4.0` | the personal subscription owner (default `az login`) | resource group `rg-p12-capstone` and the F2 capacity `capstonef2` |
| `workspace/` | `microsoft/fabric` `~> 1.14` | `fabric@` (`az login` under its own config directory) | workspace `capstone`, assigned to the F2 |

`capacity/` sets `administration_members` to `fabric@`'s UPN, so `fabric@` administers the F2 and
can assign it. `workspace/` finds the capacity by its name (a `fabric_capacity` data source), so no
capacity ID is copied between the stacks and neither is committed. The lookup is by display name,
so it can fail or pick the wrong capacity if the name is not unique to `fabric@`. If it does, set
the optional `capacity_id` in `workspace/terraform.tfvars` (the Fabric capacity GUID from the
portal, not the Azure resource ID) and the lookup is skipped. The provider schema (microsoft/fabric
1.14.0) exposes `sku` and `state` on the data source, so the lookup carries a `postcondition`: the
capacity must be an active F2 or the plan fails. That check runs only on the lookup, not when
`capacity_id` is set. The `azurerm_fabric_capacity`
arguments (`sku { name, tier }` with tier `Fabric`, `administration_members`) are those of the
pinned azurerm 4.x provider; `terraform validate` checks them.

## Values

`capacity/terraform.tfvars` (git-ignored; copy `terraform.tfvars.example`): `subscription_id` and
`capacity_admin_upn` (`fabric@`'s UPN). Neither value is ever committed. `location` is the F2's own
region, which may differ from the trial capacity's; the `australiaeast` default is an assumption to
confirm at the start of the live run. `workspace/` takes no values except the optional
`capacity_id` override above. Neither stack has outputs, so nothing prints the
subscription or the capacity ID.

## Before the first apply

The subscription must have the `Microsoft.Fabric` resource provider registered, or creating the
capacity fails. As the personal subscription owner:

```bash
az provider register --namespace Microsoft.Fabric
az provider show --namespace Microsoft.Fabric --query registrationState -o tsv   # wait for Registered
```

## Order

```bash
# Start of the session: the capacity first, as the personal subscription owner.
terraform -chdir=infra/capstone/capacity init
terraform -chdir=infra/capstone/capacity apply

# Then the workspace, as fabric@, in a shell with the other config directory.
export AZURE_CONFIG_DIR=$HOME/.azure-fabric   # az login once here, as fabric@
terraform -chdir=infra/capstone/workspace init
terraform -chdir=infra/capstone/workspace apply
```

End of the session, in the reverse order, each in its own sign-in: destroy `workspace/` before
`capacity/`. First `terraform destroy` in `workspace/` (as `fabric@`), then in `capacity/` (as the
subscription owner). The order matters: with the capacity gone first, `workspace/`'s refresh can no
longer read it and the destroy can fail; if that happens, destroy `workspace/` with `-refresh=false`. Destroy the capacity
the same day: an F2 bills while it exists, whether or not it is paused or used.

CI runs `terraform fmt -check`, `init -backend=false` and `validate` on both; nothing plans or
applies there. Each stack uses local state, which is git-ignored.
