# Fabric lakehouse deploy: research findings (as of 22 Oct 2026)

Method: fetched Microsoft Learn, microsoft.github.io/fabric-cicd, the fabric-cicd and terraform-provider-fabric GitHub repos, PyPI and the Terraform registry API. Nothing was signed in to or changed.
Tags used: [OFFICIAL] = Microsoft/project doc or source. [INFERRED] = my reading, not stated in a doc. [COMMUNITY] = non-official or old source. [NOT VERIFIED] = could not confirm. [RECENT] = changed or dated in 2026.
Learn page "updated_at" dates are quoted where they matter.

---------------------------------------------------------------------
## 1. Fabric trial

### Starting the trial
[OFFICIAL] https://learn.microsoft.com/en-us/fabric/fundamentals/fabric-trial (updated 2026-09-29; ms.date 2026-08-12)
- Method 1: Account manager (profile photo, top right of the Fabric home page) > **Start trial**, accept terms, choose a trial capacity region, **Activate**. The prompt says "Activate your 60-day free Fabric trial".
- Method 2: try to create a Fabric item in a workspace that is not on a Fabric/Trial capacity (for example My workspace). A prompt offers the trial.
- Method 3: join someone else's trial capacity.
- Starting the trial makes you Capacity administrator of that trial capacity.
- If **Start trial** is missing, the Fabric admin has disabled trials, or the tenant has hit its trial-capacity limit.
- The trial lasts 60 days. After expiry, workspaces revert to Pro, non-Power BI items go inactive, and content stays in OneLake for 7 days.
- Trial region: defaults to the tenant home region. Pick it at activation; moving a workspace later means deleting its Fabric items first.

### F4 or F64
[OFFICIAL] The doc says only that the trial "is configured as either an F4 capacity (4 capacity units) or an F64 capacity (64 capacity units)".
- It does not say what decides which one you get. Eligibility "may vary".
- If it is F4, a capacity or Fabric admin can use OneLake catalog > Govern > Capacities > **Trial** tab > **Change size** to go 4 -> 64 CUs (and back) "if eligible". If the button is absent, the doc points you to a Microsoft sales rep. Changing size does not extend the 60 days.
- https://learn.microsoft.com/en-us/fabric/enterprise/licenses lists "Trial" as 64 CUs / 8 v-cores.
- [INFERRED] Plan for F4 as the worst case: F4 means 4 CUs, which gives tiny Spark capacity.

### Free licence
[OFFICIAL] https://learn.microsoft.com/en-us/fabric/enterprise/licenses and the trial page.
- "New Power BI users: The Fabric trial requires a per-user license." You sign up for a Fabric (Free) license, then start the trial. The Free licence is auto-assigned on first sign-in to the Fabric portal.
- Table row "Create Fabric capacity workspaces": Free = yes. Free users can create non-Power BI items (lakehouses, notebooks, pipelines) in Fabric/Trial capacity workspaces.
- Free users cannot create Power BI items outside My workspace without Pro/PPU. Starting the trial also hands out a complementary Power BI Individual Trial if you have no paid Power BI licence.
- Practical conclusion: a Free-licence user can start the trial and create workspaces on it. The tenant setting "Create workspaces" must also allow the user (see section 2). Admin is the same person, so this is fine.
- [RECENT] The old trial tenant switch "Enabled for the entire organization" in the Trial section is gone. Enablement is now two tenant settings: **Users can create Fabric items** and, under Help and support settings, **Users can try Microsoft Fabric paid features**.

### Trial limits that matter to this plan
[OFFICIAL] trial page and https://learn.microsoft.com/en-us/fabric/enterprise/fabric-features
- Not supported on trial: Copilot, **Trusted Workspace Access**, AI experiences, Private Link.
- ARM APIs and Terraform: listed as F-SKU-only; the doc says trial does not support them unless noted.
- Capacity is for evaluation only; the doc says not to use it for production workloads.
- Autoscale is not supported on trial.
- Up to 1 TB OneLake storage.
- Max 3 SQL databases.

### Capacity Metrics app on a trial
[OFFICIAL] https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app-install (updated 2026-10-12)
- Prerequisites: be a **capacity admin**, and hold a Power BI licence (Pro, PPU or Power BI individual trial). Starting a Fabric trial gives you the individual trial.
- Install: AppSource > "Microsoft Fabric Capacity Metrics" > Get it now; it creates its own workspace. Then open the app, **Connect**, and fill UTC_offset (14 to -12). RegionName and DefaultCapacityID apply only to old versions (2.0 and below / 1.9.2 to 2.0). Authentication is OAuth2; privacy level is Organizational. Then pick the capacity.
- Doc says paused and trial capacities are included when determining available regions, so a trial capacity is selectable.
- Version 3.0+ adds a second report, "Fabric Capacity Metrics Preview".
- It can take a few minutes for data to appear on first open.
- Install it in a Pro-licensed workspace to avoid throttling.

---------------------------------------------------------------------
## 2. Tenant settings (OneLake catalog > Govern > Configurations > Tenant settings)

### (a) Service principals calling Fabric public APIs
[OFFICIAL] https://learn.microsoft.com/en-us/fabric/admin/service-admin-portal-developer (ms.date 2026-04-08)
- Exact name: **Service principals can call Fabric public APIs**.
  - Covers CRUD APIs protected by a Fabric permission model.
  - The SP must be in an allowed security group if the setting is scoped to groups.
  - The doc says "enabled by default for new customers". The notebook-activity page says "it's disabled by default". The docs conflict, so check the toggle yourself.
- Separate setting: **Service principals can create workspaces, connections, and deployment pipelines**.
  - Covers Create Workspace, Create Connection, Create Deployment Pipeline.
  - "Disabled by default for new customers".
  - Needed if an SP (Terraform or fabric-cicd) creates workspaces or Fabric connections.
- [COMMUNITY] Older blogs call the first one "Service principals can use Fabric APIs" or "Allow service principals to use Power BI APIs". Current UI/doc name is the one above.

### (b) GitHub sync
[OFFICIAL] https://learn.microsoft.com/en-us/fabric/admin/git-integration-admin-settings (updated 2026-10-07)
- **Users can synchronize workspace items with their Git repositories**: enabled by default.
- **Users can sync workspace items with GitHub repositories**: **disabled by default**. Must be turned on for GitHub.
  - Delegation to capacity/workspace admins is possible.
  - Scope it with "Apply to: entire organization", specific groups, or "except" groups.
- Doc anchors still carry "-preview" suffixes, but the supported-items list no longer marks GitHub as preview.

### (c) Other settings
- [OFFICIAL] https://learn.microsoft.com/en-us/fabric/cicd/git-integration/git-get-started lists: **Users can create Fabric items** (needed for Fabric artifacts); **Create workspaces** (only if you branch out to a new workspace).
- Optional git settings: "Users can export items to Git repositories in other geographical locations" (GitHub cannot enforce it) and "Users can export workspace items with applied sensitivity labels to Git repositories".
- Workspace identity: no tenant switch needed to create one.
  - A workspace admin creates it in Workspace settings > Workspace identity.
  - Optional tenant setting: "Define maximum number of Fabric identities in a tenant" (default 10,000).
  - Notebook-activity with workspace identity needs the tenant setting **Service principals can call Fabric public APIs** (per https://learn.microsoft.com/en-us/fabric/data-factory/notebook-activity).
- Terraform: no tenant switch beyond (a).
  - Terraform running as the signed-in user needs none.
  - Terraform running as an SP needs (a), plus the second SP setting if it creates workspaces/connections.
  - Provider doc for `fabric_domain`: the SP app must not hold admin-consent Fabric API permissions in Entra.
- GitHub PAT/connection: no tenant setting. The connection permission is per-connection.

---------------------------------------------------------------------
## 3. Fabric git integration with GitHub

### Item types
[OFFICIAL] https://learn.microsoft.com/en-us/fabric/cicd/git-integration/intro-to-git-integration (updated 2026-10-17)
- Lakehouse, Notebook, Environment, Data Pipeline ("Pipeline" under Data Factory), Variable Library, Spark Job Definition, Warehouse, Dataflow Gen2, Copy Job, and others are listed **without a preview tag**, so GA.
- Preview items include ML experiments/models, Airflow, dbt Job, Operations Agent, Report, Semantic model, Paginated report and others.
- Providers: Azure DevOps, GitHub, GitHub Enterprise (cloud only).
- Unsupported items in the workspace or repo are ignored: not synced, not deleted.
- The notebook source-control page (https://learn.microsoft.com/en-us/fabric/data-engineering/notebook-source-control-deployment) still says "Git integration with Azure DevOps". That text is stale; the main git docs list GitHub.
- Lakehouse git tracking (https://learn.microsoft.com/en-us/fabric/data-engineering/lakehouse-git-deployment-pipelines, updated 2026-10-13):
  - GA: lakehouse metadata, shortcut definitions (`shortcuts.metadata.json`).
  - Preview: OneLake data-access roles (`data-access-roles.json`).
  - Not tracked: table data, files, Spark views, shortcut transformations.
  - Config file: `alm.settings.json`.
- [RECENT] Starting **1 Dec 2026**, users without read-write permission on workspace items cannot use Git integration (https://learn.microsoft.com/en-us/fabric/cicd/git-integration/git-integration-process).

### PAT requirements
[OFFICIAL] https://learn.microsoft.com/en-us/fabric/cicd/git-integration/git-get-started and git-integration-process
- Fine-grained token (recommended): repository permission **Contents: Read and write**. Read is enough for connect/sync/update; commit needs read and write.
- Classic token: **repo** scope.
- Entering a repo URL when adding the account scopes the connection to that one repo.
- The Git connection is per user. Do not share one PAT across users.
- Only a workspace Admin can connect, disconnect or change branch. Contributors can switch branch only if the workspace setting "Allow users with at least Contributor role to change Git branch" is on.
- Limits on GitHub: commit is limited to 50 MB combined; files to 25 MB; branch name to 244 characters; paths to 250 characters.
- Via REST (https://learn.microsoft.com/en-us/rest/api/fabric/core/git/connect): for GitHub, `myGitCredentials` is required and `source` must be `ConfiguredConnection`.
  - You first create a Fabric connection: POST /v1/connections with `connectionDetails.type = "GitHubSourceControl"`, `creationMethod = "GitHubSourceControl.Contents"`, credentials `credentialType = "Key"` and the PAT as `key` (https://learn.microsoft.com/en-us/fabric/cicd/git-integration/git-automation).
  - "Automatic" credentials are blocked for GitHub and for service principals.

### Non-default branch
[OFFICIAL] git-get-started: you pick any existing branch, or "+ New Branch". It is not restricted to the default branch.
- One branch and one folder per workspace.
- "Checkout new branch" and "Branch out to another workspace" create branches from within Fabric.

### Branches protected by rulesets that require PRs
[OFFICIAL] git-integration-process, Git roles table, GitHub tab: "Commit workspace changes to Git: Contents = Access: Read and write; **branch policy should allow direct commit**". The Azure Repos row says the same.
- So Fabric cannot commit through a PR requirement. Commits are direct pushes made with the user's PAT.
- Documented workaround: use **Checkout new branch** or **Commit to new branch** (a stand-alone branch), then open a PR. The doc recommends this exactly when "you can't make changes to the connected branch directly, due to branch policy".
- The new stand-alone branch is not connected to the workspace.
- [NOT VERIFIED] Whether a ruleset bypass-actor entry for the PAT owner would let direct commits through. The docs don't say.
- Plan implication: connect the dev workspace to a feature/dev branch that is not PR-protected, then PR to main.
- Prod deploys with fabric-cicd do not use workspace git sync, so prod is unaffected.

### Notebook format in git
[OFFICIAL] notebook-source-control-deployment (updated 2026-08-04)
- Folder `<Name>.Notebook/` containing `.platform`, `notebook-content.py` (`.sql` for SQL notebooks), and optional `notebook-settings.json`, `fs-settings.json`, `Resources/builtin/...`.
- Stored as source files, not `.ipynb`. The `.py` keeps metadata (default lakehouse, attached environment), markdown cells and code cells as separate sections.
- "Notebook cell output isn't included when syncing to Git." So outputs are not stored.
- Physical lakehouse/environment IDs are replaced by logical IDs on commit. This shows as diff noise.
- Optional "Lakehouse auto-binding" writes `notebook-settings.json`; do not hand-edit it.
- fabric-cicd's own example regex confirms the default-lakehouse key: `# META  "default_lakehouse": "<guid>"` (https://microsoft.github.io/fabric-cicd/latest/how_to/parameterization/).
- Notebook Resources folder is committed only if you opt in (50 MB commit limit).
- fabric-cicd supports `.py` and `.ipynb` notebooks (`.ipynb` added in 0.3.0).

### Lakehouse shortcuts in git
[OFFICIAL] lakehouse-git-deployment-pipelines and https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/lakehouse-definition
- File: `shortcuts.metadata.json`, in the lakehouse folder.
- Contents: JSON array of `{ name, path, target }`.
  - `target.type` is one of OneLake / AdlsGen2 / AzureBlobStorage / AmazonS3 / S3Compatible / GoogleCloudStorage / Dataverse / OneDriveSharePoint.
  - ADLS Gen2 target object: `adlsGen2: { location, subpath, connectionId }` (all required).
  - OneLake target object: `oneLake: { workspaceId, itemId, path }`; empty GUIDs mean "this workspace/lakehouse".
- The documented sample only shows an OneLake shortcut. By the schema, external shortcuts DO serialise the target storage URL (`location`, e.g. `https://acct.dfs.core.windows.net`), the container/path (`subpath`), and the Fabric `connectionId` GUID. No secrets are stored (the credential lives in the connection), but the account name and connection GUID land in the repo.
- Same targets are kept across stages unless you remap them.
- A git update **overrides the workspace's shortcut state**: shortcuts are created, updated and deleted to match git.
- Tracking toggles live in `alm.settings.json`.
  - The doc's example lists sub-types Shortcuts.OneLake / AdlsGen2 / Dataverse / AmazonS3 / S3Compatible / GoogleCloudStorage.
  - It does not list AzureBlobStorage or OneDriveSharePoint, although the git page says Blob is tracked. [NOT VERIFIED] whether Blob shortcuts serialise in practice.

---------------------------------------------------------------------
## 4. fabric-cicd

### Version and Python
[OFFICIAL] PyPI JSON https://pypi.org/pypi/fabric-cicd/json and https://github.com/microsoft/fabric-cicd/releases
- Latest: **1.4.0** (uploaded 2026-10-07). Earlier: 1.3.0 (2026-08-10), 1.2.0 (2026-06-30), 1.1.0 (2026-05-27), 1.0.0 (2026-04-20), 0.3.1 (2026-03-12), 0.3.0 (2026-03-09), 0.2.0 (2026-02-18).
- `requires_python`: **>=3.9, <3.15** (docs say 3.9 to 3.14). Python 3.13 support arrived in 0.3.0; 3.14 in 1.4.0.
- Dependencies: azure-identity>=1.25.0, dpath, filetype, jsonpath-ng (>=1.7.0,<1.8.0), pyyaml>=6.0.3, requests>=2.32.3, urllib3>=2.7.0 (Python 3.10+ only).
- [RECENT] 1.0.0 (Apr 2026) was breaking:
  - the default-credential fallback was removed;
  - an explicit `token_credential` is now required;
  - `FabricWorkspace` and `deploy_with_config` take keyword-only arguments;
  - implicit auth inside Fabric notebooks was removed.
- Other notable recent changes:
  - 1.1.0: Environment Spark compute now deployed via the item definition API.
  - 1.2.0: bulk publish mode, dynamic variables allowed in `find_replace` `find_value`.
  - 1.4.0: dynamic variables work in bulk publish; `$ENV:` uses plain OS env var names.

### Supported item types
[OFFICIAL] https://microsoft.github.io/fabric-cicd/latest/
ApacheAirflowJob, CopyJob, CosmosDBDatabase, DataAgent, DataBuildToolJob, DataPipeline, Dataflow, Environment, Eventhouse, Eventstream, GraphModel, GraphQLApi, KQLDashboard, KQLDatabase, KQLQueryset, Lakehouse, Map, MirroredDatabase, MLExperiment, MountedDataFactory, Notebook, Ontology, PaginatedReport, Reflex, Report, SemanticModel, SparkJobDefinition, SQLDatabase, UserDataFunction, VariableLibrary, Warehouse.
- Only items with Source Control support plus public create/update APIs are supported.
- Item-specific notes: https://microsoft.github.io/fabric-cicd/latest/reference/item_types/
  - **Lakehouse**: shortcut publish is **off by default**; enable with feature flag `enable_shortcut_publish`. Schemas deploy only if a shortcut exists. Lakehouse unpublish needs `enable_lakehouse_unpublish`.
  - **Notebook**: stays attached to the original lakehouse unless parameterized. Resources are not deployed.
  - **Environment**: "Resources are not source controlled and will not be deployed". Custom-pool IDs need `spark_pool` parameterization.
  - **DataPipeline**: same-workspace item references are auto-repointed; cross-workspace ones are not. Connections are not source controlled, and "The executing identity of the deployment must have access to the connections".
- Source (https://github.com/microsoft/fabric-cicd/blob/main/src/fabric_cicd/_items/_lakehouse.py): if `lakehouse.metadata.json` contains `defaultSchema`, the create call sends `creationPayload {"enableSchemas": true}`. So a schema-enabled lakehouse stays schema-enabled on deploy.

### parameter.yml
[OFFICIAL] https://microsoft.github.io/fabric-cicd/latest/how_to/parameterization/
- Placed in the root of `repository_directory`.
- The key that matches the `environment` passed to `FabricWorkspace` (e.g. `PPE`, `PROD`) is used. If the key is missing, that replacement is skipped.
- Sections: `find_replace`, `key_value_replace`, `spark_pool`, `semantic_model_binding`.
- `find_replace`:
  - fields `find_value`, `replace_value`; optional `is_regex`, `ignore_case`, `item_type`, `item_name`, `file_path`.
  - `is_regex: "true"` uses capture group 1 as the part replaced.
  - Cannot combine regex with a variable in `find_value`.
- `key_value_replace`:
  - `find_key` is a JSONPath. Works on any JSON/YAML file (e.g. `.schedules`) but not on `.platform`.
  - `find_key` accepts no variables; `replace_value` does.
  - Example: `$.properties.activities[?(@.name=="Run Notebook")].typeProperties.workspaceId`.
- Per-environment values: `replace_value: { PPE: ..., PROD: ... }`. `_ALL_` (case-insensitive) applies one value to every environment and must be the only key.
- Dynamic tokens usable in `replace_value`:
  - `$workspace.$id` (or `$workspace.id`), `$workspace.$name`, `$workspace.$name_encoded`, `$workspace.<name>.$id`;
  - `$items.<type>.<name>.$id`, `$items.Lakehouse.<name>.$sqlendpoint`, `$items.Lakehouse.<name>.$sqlendpointid`, `$items.Eventhouse.<name>.$queryserviceuri`.
  - Item types and names are case-sensitive; attributes are lowercase; `$items` resolves only items present in the repo.
- `$ENV:` tokens need feature flag `enable_environment_variable_replacement`. Use the `$ENV:` prefix in the file but the plain name in the shell.
- Filters `item_type`, `item_name`, `file_path` are case-sensitive. `file_path` accepts globs (`**/notebook-content.py`).
- Source shows `shortcuts.metadata.json` gets `_replace_parameters` applied during shortcut publish, so `connectionId` and `location` can be parameterized. [INFERRED] The item-types page says "find_replace not applied" for Lakehouse, which refers to the item definition itself.

### Auth in GitHub Actions with OIDC
[OFFICIAL] https://microsoft.github.io/fabric-cicd/latest/example/release_pipeline/ and https://microsoft.github.io/fabric-cicd/latest/example/authentication/
- Workflow: `permissions: id-token: write, contents: read`; `pip install fabric-cicd`; `azure/login@v2` with `client-id`, `tenant-id`, `subscription-id` from secrets; then `python .deploy/fabric_workspace.py`.
- Python credential: **`AzureCliCredential()`** ("assumes 'az login' in workflow step"). The doc calls this the Azure CLI Credential Flow and says an explicit credential is required.
- You must configure a **federated identity credential** on the Entra app registration trusting GitHub's OIDC issuer. The doc links to the "Azure login with OIDC" guide. GitHub's guide recommends audience `api://AzureADTokenExchange`, with subject conditions to restrict repos.
- Other credentials the docs list: AzurePowerShellCredential, ClientSecretCredential, ManagedIdentityCredential. The troubleshooting page also mentions `WorkloadIdentityCredential` for OIDC.
  - [INFERRED] `WorkloadIdentityCredential` expects a token file as on AKS, so it is not the natural fit for GitHub-hosted runners.
  - Use `azure/login` + `AzureCliCredential`.
- [INFERRED] The SP may have no Azure subscription role. In that case `azure/login` needs `allow-no-subscriptions: true`. Fabric needs no Azure RBAC. Not in the fabric-cicd docs.

### SP roles
- The fabric-cicd docs do not state a required workspace role or any tenant setting (the Authentication, Getting Started and release_pipeline pages are all silent).
- Required in practice [INFERRED from Fabric API rules and docs elsewhere]:
  - The SP must be added to the target workspace (Manage access or Terraform) as **Contributor or higher**, with tenant setting (a) allowing it.
  - **Admin** is needed for `enable_hard_delete` (documented in the fabric-cicd optional features).
  - Pipelines deployed by the SP: the SP needs access to the connections they reference.
  - Shortcut creation through the API needs the SP to have permission on the connection. [INFERRED]
- [RECENT] Deployed notebooks run under "the pipeline's last modified user", so a pipeline deployed by an SP runs its notebook activity as the SP. See section 9.

---------------------------------------------------------------------
## 5. Terraform `microsoft/fabric`

### Version
[OFFICIAL] https://registry.terraform.io/v1/providers/microsoft/fabric and https://github.com/microsoft/terraform-provider-fabric/releases
- Current: **1.14.0** (2026-09-17). Previous: 1.13.0 (2026-08-17; added Spark runtime 2.0 in `fabric_spark_workspace_settings`), 1.12.1 (2026-08-06), 1.12.0 (2026-07-08).
- Several features need `provider "fabric" { preview = true }`.

### Resources and data sources
[OFFICIAL] https://github.com/microsoft/terraform-provider-fabric/tree/main/docs (main branch, fetched 22 Oct 2026)
- Workspace: `fabric_workspace` (args `display_name`, `description`, `capacity_id`, `identity { type = "SystemAssigned" }`, `skip_capacity_state_validation`).
- Role assignment: `fabric_workspace_role_assignment` (`principal { id, type }` with `type` of Group / ServicePrincipal / ServicePrincipalProfile / User; `role` of Admin / Contributor / Member / Viewer).
- Capacity lookup: data sources `fabric_capacity` (by `display_name` or `id`; returns `sku`, `state`, `region`) and `fabric_capacities`.
- Git connection: `fabric_workspace_git`.
- Lakehouse: `fabric_lakehouse` (`configuration.enable_schemas = true`, `definition` map accepting `alm.settings.json`, `data-access-roles.json`, `lakehouse.metadata.json`, `shortcuts.metadata.json`).
- Shortcut: `fabric_shortcut` (target blocks `onelake`, `adls_gen2`, `amazon_s3`, `google_cloud_storage`, `s3_compatible`, `dataverse`; an `azure_blob_storage` example appears further down the page).
- Connection: `fabric_connection` (`credential_type` includes `WorkspaceIdentity`, `ServicePrincipal`, `Key`, `Basic`, and others; OAuth2 is not supported).
- Environment: `fabric_environment`; also `fabric_spark_environment_settings`, `fabric_spark_workspace_settings`, `fabric_notebook`, `fabric_data_pipeline`, `fabric_variable_library`, `fabric_tenant_setting`.
- Workspace managed identity: there is no separate resource. It is the `identity` block on `fabric_workspace`. Read-only attributes `application_id` and `service_principal_id` are exported. The doc examples show it with and without a capacity.
- 1.14.0 added `fabric_workspace_encryption`, `fabric_workspace_firewall_rules`, `fabric_workspace_inbound_azure_resource_rules`, `fabric_paginated_report`.

### Trial capacity
[OFFICIAL] provider index https://github.com/microsoft/terraform-provider-fabric/blob/main/docs/index.md, "Known limitations":
> "**Capacity**: Microsoft Fabric trial capacity is not supported. Only self-provisioned Fabric Capacity on Azure is supported."
- So the `fabric_capacity` data source and `fabric_workspace.capacity_id` against a trial capacity are not supported.
- [NOT VERIFIED] I did not test whether the underlying List Capacities API returns trial capacities to the data source. No GitHub issue about it was found.
- Learn's Features-by-SKU page also says Terraform (the ARM/azapi side) is F-SKU only.
- Practical fallback for the trial: create the workspace and assign the capacity in the UI or REST, or create the workspace without `capacity_id` and assign the trial capacity manually. Both are [INFERRED], not tested.

### Authentication
[OFFICIAL] provider index
- Azure CLI (default; the doc recommends "Azure CLI for authentication with User context" for evaluation).
- Also managed identity (`use_msi`), OIDC (`use_oidc`, `oidc_token`, `oidc_token_file_path`), client certificate, client secret, dev CLI (`use_dev_cli`).
- Known limitation: "Not all Fabric resources support Service Principals yet."
- Each resource doc carries a line "This resource supports Service Principal authentication". I scanned all 198 doc files. Only these are marked otherwise:
  - **`fabric_workspace_git`**: SP works only when `git_credentials.source = "ConfiguredConnection"`. Azure DevOps with `Automatic` credentials is user-only. **GitHub supports only `ConfiguredConnection`**, which needs a `connection_id` (a Fabric connection holding the GitHub PAT).
  - A handful of preview/AI resources (anomaly_detector, digital_twin_builder, operations_agent, tag, and several data sources: dashboards, datamarts, mirrored_warehouses, sql_endpoints) carry no SP note.
  - The ones this plan needs (workspace, role assignment, lakehouse, shortcut, connection, capacity) all say SP is supported.
- Azure CLI as a user works for everything above. For `fabric_workspace_git` with GitHub the connection must exist first.
  - Create it by `fabric_connection` (a GitHub source-control connection type with a PAT as `Key` credential) or in the UI.
  - [NOT VERIFIED] whether `fabric_connection` can create the `GitHubSourceControl` type end to end; the API doc shows it can.

---------------------------------------------------------------------
## 6. Environment item and custom Python libraries

### Options for a private wheel
[OFFICIAL] https://learn.microsoft.com/en-us/fabric/data-engineering/library-management and https://learn.microsoft.com/en-us/fabric/data-engineering/environment-manage-library (updated 2026-10-20)
- Environment > Custom libraries > upload `.whl` (also `.py`, `.jar`, `.tar.gz` for R). Python custom libraries must be `.whl` or `.py`.
- Inline `%pip install "builtin/<wheel>.whl"` from the notebook Resources folder is per session/notebook and is not affected by environment publishing.
- **Inline `%pip` is disabled by default in pipeline runs.** Enable it by adding a boolean notebook-activity parameter `_inlineInstallationEnabled = True`. Microsoft recommends the Environment for pipelines.
- `%pip install` is not supported in High Concurrency mode.
- `%pip install` from an `abfss://` or OneLake Files path is not shown in these docs. The documented inline route is the Resources folder. [NOT VERIFIED] for OneLake Files.
- Notebook Resources limits: 500 MB total, 100 MB per file, 100 files.
- Environment Resources are also not deployed by git or fabric-cicd.

### Publish modes
- **Full mode**: publish "typically takes 3 to 6 minutes"; session start adds 1 to 3 minutes. Recommended for pipelines, scheduled runs and shared workloads.
- **Quick mode**: publish about 5 seconds; libraries install at notebook session start; **notebooks only, not Spark job definitions**.
- Custom `.whl`/`.py`/`.tar.gz` support Full and Quick; custom `.jar` is Full only.
- Moving a library between modes means download, delete and re-upload.
- The Environment accepts only one publish at a time.

### Runtime 2.0
[OFFICIAL] https://learn.microsoft.com/en-us/fabric/data-engineering/runtime-2-0 (ms.date 2026-08-17, updated 2026-09-18)
- Runtime 2.0 = Spark 4.1, Delta 4.2, Python 3.13, Java 21, Scala 2.13, Azure Linux 3.0. It is **GA**.
- Select it in the Environment's Runtime dropdown or the workspace Spark settings.
- [RECENT] The doc says Runtime 2.0 would become the default for new workspaces and environments in "late September 2026". I could not confirm that this happened; check the dropdown.
- Delta 4.x-specific features are "experimental" and only work in Spark experiences. Do not enable them if the tables must be read by other Fabric workloads (SQL endpoint, Direct Lake).
- No document I found says custom `.whl` libraries are unsupported on Runtime 2.0 Environments. The Environment pages list per-source notes instead.
  - JFrog Artifactory needs Runtime 2.0 or later.
  - Maven `pom.xml` import needs Spark 4.0 or later.
  - Azure Artifact Feed is documented only for Spark 3.5.
  - So treat custom wheels on Runtime 2.0 as supported but test it on the real wheel. [INFERRED]
- Your wheel must be built for Python 3.13 (cp313 or pure-Python `py3-none-any`).
- Terraform 1.13.0 added Runtime 2.0 to `fabric_spark_workspace_settings`.

### fabric-cicd and Environments with wheels
[OFFICIAL] https://microsoft.github.io/fabric-cicd/latest/reference/item_types/ and source https://github.com/microsoft/fabric-cicd/blob/main/src/fabric_cicd/_items/_environment.py
- Git stores the wheel inside the item: `Libraries/CustomLibraries/<wheel>`, `Libraries/PublicLibraries/environment.yml`, `Setting/Sparkcompute.yml` (https://learn.microsoft.com/en-us/fabric/data-engineering/environment-git-and-deployment-pipeline). Each git commit is limited to 150 MB for Environments (a separate 50 MB limit applies to GitHub commits overall).
- The Environment definition API accepts `Libraries/CustomLibraries/<name>.whl` as a Base64 part (https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/environment-definition).
- fabric-cicd creates/updates the item with its definition (so the wheel travels with it), then calls `POST .../environments/{id}/staging/publish?beta=False` and polls `properties/publishDetails/state` until Success/Failed/Cancelled.
- The fabric-cicd doc warns "Environments with libraries will have high initial publish times (sometimes 20+ minutes)". That is far more than Microsoft's 3 to 6 min for Full mode.
- The pool ID in `Sparkcompute.yml` needs `spark_pool` parameterization if you use a custom pool.
- Which publish mode (Quick or Full) results from the definition-API route is not documented. [NOT VERIFIED]
- [RECENT] The Environment REST API GA migration: the `beta` query parameter defaulted to true until 31 Aug 2026, and the old "Upload/Delete staging libraries" APIs were due to be dropped on that date (https://learn.microsoft.com/en-us/fabric/data-engineering/environment-public-api). fabric-cicd 1.4.0 uses `beta=False`.
- A single upload through the public upload-library API is capped at 200 MB (git limit is 150 MB).

---------------------------------------------------------------------
## 7. OneLake shortcuts to Azure storage

### ADLS Gen2 and Blob
[OFFICIAL] https://learn.microsoft.com/en-us/fabric/onelake/create-adls-shortcut (updated 2026-07-01) and https://learn.microsoft.com/en-us/fabric/onelake/shortcuts/create-blob-shortcut (updated 2026-07-01)
- Both shortcut types exist and both are listed as supported external shortcuts in git (GA).
- **ADLS Gen2**:
  - Prerequisite: "You must enable Hierarchical Namespaces on your ADLS Gen2 storage account".
  - URL must be the DFS endpoint `https://<acct>.dfs.core.windows.net`.
- **Azure Blob Storage**:
  - Prerequisite: only "An Azure Storage account with data in a container". No HNS requirement is stated.
  - URL is `https://<acct>.blob.core.windows.net` or just the account name.
  - So a non-HNS storage account (such as one holding Cost Management exports) needs the Blob shortcut type, not the ADLS one. [INFERRED from the absence of the HNS requirement]
- ADLS limits:
  - Target paths cannot contain RFC 3986 reserved characters.
  - Shortcuts to storage accounts that use **managed private endpoints are not supported**.
  - Cross-tenant ADLS shortcuts need SP or SAS.
  - Copy Blob API unsupported; do not copy from a shortcut pointing straight at a container root (point it at a folder at least one level below).
- Shortcuts in Tables can only sit at the top level of Tables.

### Authentication with workspace identity
[OFFICIAL] https://learn.microsoft.com/en-us/fabric/security/workspace-identity-authenticate (updated 2026-08-19) and workspace-identity (updated 2026-09-29)
- Create the identity: workspace Admin only, Workspace settings > Workspace identity > "+ Workspace identity". The name equals the workspace name. It is a service principal plus an app registration.
- Role: **Storage Blob Data Reader / Contributor / Owner at the storage-account level** (the doc says "The role must be provided at the Storage account level"). Alternatively **Delegator** on the account plus ACLs on folders.
  - The Entra-based delegated types need the "Generate a user delegation key" action. Reader, Contributor, Owner and Delegator all include it.
  - The doc says this requirement is not enforced yet when a workspace identity exists, but "will be restricted in the future".
- Delegated model: whoever creates the shortcut chooses the credential, and all access through that shortcut uses it.
- Creating the shortcut with workspace identity needs the workspace role Admin, Member or Contributor.
- The identity that **runs a pipeline** must also hold Admin/Member/Contributor on the workspace, or workspace-identity tokens are not issued. This applies to SPs and schedules too.
- Doc inconsistency: the workspace-identity doc says "supported only for ADLS Gen2 shortcuts", while the Blob shortcut page lists "workspace identity" as a supported auth type. Test it for Blob.
- **Trusted workspace access** (shortcut access to a firewall-enabled storage account) needs a **purchased F-SKU capacity**; trial does not support it. The workspace identity itself can be created and used as a connection credential on any capacity.
- Cross-tenant and B2B are not supported for workspace identity.

### REST API
[OFFICIAL] https://learn.microsoft.com/en-us/rest/api/fabric/core/onelake-shortcuts/create-shortcut (updated 2026-09-15)
- `POST https://api.fabric.microsoft.com/v1/workspaces/{workspaceId}/items/{itemId}/shortcuts[?shortcutConflictPolicy=Abort|GenerateUniqueName|CreateOrOverwrite|OverwriteOnly]`
- Scope: `OneLake.ReadWrite.All`. Service principals and managed identities are supported.
- Body: `path` (e.g. "Files/landingZone" or "Files"), `name`, and `target` with exactly one key. Example payloads:

```json
{ "path": "Files/landingZone", "name": "PartnerProducts",
  "target": { "adlsGen2": { "location": "https://acct.dfs.core.windows.net",
                            "subpath": "/mycontainer/data/ContosoProducts",
                            "connectionId": "<guid>" } } }
```
```json
{ "path": "Files", "name": "MyAzureBlobStorage",
  "target": { "azureBlobStorage": { "location": "https://acct.blob.core.windows.net",
                                     "subpath": "/tables", "connectionId": "<guid>" } } }
```
- `connectionId` is a pre-existing Fabric cloud connection (create it in Manage connections, or via POST /v1/connections with credentialType WorkspaceIdentity). The API does not accept inline credentials.
- Also available: Create Shortcuts In Bulk.
- Limits: shortcut names, parent paths and target paths cannot contain "%" or "+"; no non-Latin characters; max 10 shortcuts per OneLake path; up to 100 unique connections per item.

### Does an API-created shortcut appear in git?
[OFFICIAL] lakehouse-git-deployment-pipelines
- Shortcuts are tracked in git (GA) when "Shortcuts" tracking is on (default for new lakehouses). Additions, deletions and updates are "tracked automatically".
- So a shortcut created by API shows up as an uncommitted lakehouse change in the Source control panel and is written to `shortcuts.metadata.json` when you commit.
- Conversely "Update from Git" can delete API-created shortcuts that are not in git ("all shortcuts are created, updated, or deleted based on the incoming state from git").
- [INFERRED] Treat git as the source of truth for shortcuts, or turn off shortcut tracking in the lakehouse settings.

---------------------------------------------------------------------
## 8. Notebook paths and file/table access

[OFFICIAL] https://learn.microsoft.com/en-us/fabric/data-engineering/notebookutils/notebookutils-file-system, https://learn.microsoft.com/en-us/fabric/data-engineering/how-to-use-notebook, https://learn.microsoft.com/en-us/fabric/onelake/onelake-access-api, https://learn.microsoft.com/en-us/fabric/data-engineering/lakehouse-schemas

### Relative paths
- With a default lakehouse pinned, a relative path in a **Spark notebook** such as `Files/x` or `Tables/y` resolves to the default lakehouse's ABFS path (the doc says "the default lakehouse is served as the root folder at run time").
- In a **Python notebook** (non-Spark) a relative path resolves to the local working directory `/home/trusted-service-user/work`. Use the full `/lakehouse/default/Files/...` there.
- "Reading/writing with a relative path is not functioning in a High concurrency session".
- You must restart the session after pinning a new default lakehouse or renaming it.
- `notebookutils.fs`: relative path on a non-default lakehouse is "Not supported"; use abfss.
- Spark SQL: the default lakehouse picks the Hive metastore. If several lakehouses are attached, make sure the target and the default are in the same workspace.

### Full OneLake path
- `abfss://<workspace>@onelake.dfs.fabric.microsoft.com/<lakehouse>.Lakehouse/Files/...` and `.../Tables/...`
- Per the OneLake doc, the `<item>.<itemtype>` suffix is required in name-based URIs. Workspace names with special characters or spaces do not work with abfs; use the GUID form `abfss://<workspaceGUID>@onelake.dfs.fabric.microsoft.com/<itemGUID>/Files/...` (no item type needed with GUIDs).
- Tokens must be in the `Storage` audience.
- The `.Lakehouse` casing is from my memory; the doc uses lowercase `.lakehouse`. OneLake is case-insensitive. [NOT VERIFIED]

### `/lakehouse/default/Files` mount
- "Your default is then mounted to the runtime working directory, and you can read or write to the default lakehouse using a local path."
- It requires a default lakehouse.
- The doc's example uses it for Python notebooks, e.g. `notebookutils.fs.ls("/lakehouse/default/Files")`.
- For the default lakehouse, the file cache timeout is 120 seconds, so deleted files may linger briefly.
- Only the Files section is documented as a plain local path. Tables are not meant to be written as files.

### notebookutils.fs
- `put(file, content, overwrite=False)`: writes a UTF-8 string, no concurrent writes, not atomic.
- `cp(src, dest, recurse=False)`: in Python notebooks it uses the azcopy mechanism, same as `fastcp`.
- Also `mv`, `ls`, `mkdirs`, `head`, `append`, `rm`, `exists`.
- Path schemes: relative (Spark notebooks), `abfss://...`, `file:/...` (driver local).
- The `create_path` default for `mv` differs by notebook type (False in Spark, True in Python); set it explicitly.
- The package is named `notebookutils` (the `mssparkutils` name is being retired).

### Schema-enabled lakehouses
[OFFICIAL] https://learn.microsoft.com/en-us/fabric/data-engineering/lakehouse-schemas (updated 2026-08-04). The page carries no preview tag. GA announcement is in the Fabric blog (https://blog.fabric.microsoft.com/en-us/blog/lakehouse-schemas-generally-available), which I only saw in search results; the date was not confirmed (probably late 2025). [COMMUNITY]
- Schemas are on **by default** when creating a lakehouse in the portal. When creating through the **REST API they are off by default**; pass `"creationPayload": {"enableSchemas": true}`.
- Every schema-enabled lakehouse has a non-removable `dbo` schema.
- Table paths become `Tables/<schema>/<table>`. A bare table name resolves to `dbo` of the default lakehouse.
- Code: `df.write.saveAsTable("marketing.productdevelopment")`.
- Four-part Spark SQL names `workspace.lakehouse.schema.table`.
- Non-schema lakehouses are referenced as `workspace.lakehouse.table`.
- To use schemas in notebook code the default lakehouse must be schema-enabled or none is selected.
- Limitations: a schema-enabled lakehouse cannot be shared directly through workspace-level sharing; external ADLS table metadata is unsupported directly (use shortcuts).
- No migration tool yet for existing lakehouses (so decide at creation time).
- Your git `lakehouse.metadata.json` with `"defaultSchema": "dbo"` makes fabric-cicd create a schema-enabled lakehouse.
- Schema shortcuts work only in schema-enabled lakehouses.

---------------------------------------------------------------------
## 9. Data pipelines

[OFFICIAL] https://learn.microsoft.com/en-us/fabric/data-factory/notebook-activity (updated 2026-07-23), https://learn.microsoft.com/en-us/fabric/data-factory/activity-overview, https://learn.microsoft.com/en-us/fabric/data-factory/activity-retries (updated 2026-08-26), https://learn.microsoft.com/en-us/fabric/data-factory/pipeline-runs (updated 2026-09-29)

### Notebook activity and base parameters
- Settings tab: Connection (auth method for the run), Notebook picker, **Base parameters**, optional session tag.
- Base parameters are auto-populated from the notebook's **parameter cell** (names, types, defaults). The cell must be marked as a parameter cell; with no parameter cell nothing is populated.
- Session tag reuses a Spark session and requires the workspace setting "High concurrency mode for pipelines running multiple notebooks".

### Security context
- From https://learn.microsoft.com/en-us/fabric/data-engineering/how-to-use-notebook: a pipeline-run notebook executes as "the identity of the **pipeline's last modified user**", and a scheduled run executes as the user who created or last updated the schedule.
- If fabric-cicd (the SP) deploys the pipeline, the SP becomes that identity, so the SP needs data access (workspace role) and, for workspace-identity connections, an Admin/Member/Contributor role.
- Known issue: an SP running notebooks with Semantic Link has limited support.

### Retry settings
- General tab: Timeout (default 12 h, max 7 days; format D.HH:MM:SS), **Enable retries**, **Retry** count (1 to 1000; doc says default 1; a note on the general page says "Defaults to 1"), Retry interval (default 30 s fixed) or **Increasing Delay** (exponential back-off with Max retry interval default 3600 s).
- Retry-interval fields do not accept dynamic expressions.
- Retry conditions (preview) match on error code, error message or failure type (supported for Copy, Notebook, Dataflow, Stored procedure).
- Retry is on every failure unless conditions are set.
- Secure input and secure output toggles exist.

### Pipeline parameters and passing a date
- Pipeline parameters are referenced as `@pipeline().parameters.<name>` and passed into the Notebook activity base parameters (type String).
- Scheduled runs can pass parameter values: parameter names in the schedule must match exactly (mismatches are ignored); values can be literal or from a Variable Library.
- Expression language (https://learn.microsoft.com/en-us/fabric/data-factory/expression-language): `@pipeline().TriggerTime` (UTC ISO-8601), `utcNow()`, `addDays(...)`, `formatDateTime(<ts>, 'yyyy-MM-dd')`.
  - A usable expression: `@formatDateTime(pipeline().TriggerTime,'yyyy-MM-dd')`. The doc shows the pieces, but this exact combination is my illustration, not a quoted example.
- Interval-based schedules (preview) expose Window start/end trigger parameters. They cannot be edited/enabled/disabled after creation, and have no backfill.

### Failure behaviour
- The Learn pages I fetched do not spell this out for Notebook activities. [INFERRED from standard pipeline semantics]: an unhandled exception in the notebook marks the Notebook activity Failed, and the pipeline run fails unless a downstream "on completion/failure" branch handles it.
- `notebookutils.notebook.exit(value)` in a pipeline-run notebook completes normally with the exit value (it does not fail the run).
- Do not call `exit()` inside try/except, or it will not take effect.
- To fail deliberately, let the exception propagate (`raise`).
- Pipeline editor also has a **Fail** activity.

### Scheduling
- Multiple schedules per pipeline (up to 20), fixed schedule or interval-based (preview), with **start and end dates both required** (no open-ended schedule; use e.g. 01/01/2099), time zone, enable/disable toggle.
- Failure email notifications only for scheduled runs, not on-demand.
- Schedules are stored in the pipeline's `.schedules` file in git. fabric-cicd can toggle them per environment with `key_value_replace` on `$.schedules[?(@.jobType=="Execute")].enabled`.

---------------------------------------------------------------------
## 10. Azure Cost Management exports (pay-as-you-go that was a Free Trial until 30 Sep 2026)

### Biggest caveat first
[OFFICIAL] https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/understand-cost-mgt-data (ms.date 2025-06-27, updated 2026-06-02)
- Free Trial (MS-AZR-0044P) and Pay-as-you-go (MS-AZR-0003P) are both supported offers, with data from 2 Oct 2018.
- But: "**If a subscription changes offers, costs before the offer change date aren't available.**" A Free Trial to pay-as-you-go upgrade changes the offer. So pre-30-Sep-2026 costs might not be retrievable in Cost Management or exports.
- Free-trial history "might not match your invoice" because credits are excluded.
- [NOT VERIFIED] Whether a Free Trial upgrade counts as an offer change for this rule. Test with one small export covering Sept 2026.

### One-time export over a past date range
[OFFICIAL] https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/tutorial-improved-exports (the doc's own dates: ms.date 2025-06-26, updated 2025-09-26; may predate current behaviour) and https://learn.microsoft.com/en-us/azure/cost-management-billing/automate/tutorial-seed-historical-cost-dataset-exports-api
- Frequency options for Cost and usage details (actual): One-time export, daily month-to-date, monthly of last month, monthly of last billing month.
- Past dates: portal action **Export selected dates** reruns an export for history, "up to 13 months of historical data in one-month chunks" (one month at a time). Not available for price sheets.
- REST API: up to **7 years** for Cost and usage (Actual/Amortized/FOCUS). Use Exports - Create Or Update with `timeframe: "Custom"` and `timePeriod {from,to}`, then Exports - Execute.
  - Microsoft recommends no more than one month per report; 12 requests for one year. Current REST API version shown on Learn is `2026-06-01`; exports behind a firewall need API version **2023-08-01 or later**.
  - Example body: `definition.type "ActualCost"`, `timeframe "Custom"`, `dataSet.granularity "Daily"`, `format "Csv"`, `partitionData` true/false, `deliveryInfo.destination {resourceId, container, rootFolderPath}`.
- Scopes: subscription, resource group, management group (EA only), etc. Reader on the subscription can schedule exports they have permission to.
- Data latency: pay-as-you-go can take up to 72 hours; a brand-new subscription up to 48 hours before Cost Management works at all. Exports can take up to 24 hours to complete; data should appear within 4 hours of run start.

### File formats and layout
- CSV (compression None or **Gzip**) or **Parquet** (None or **Snappy**). Datasets such as reservation exports are CSV only.
- **File partitioning is always on** (cannot be disabled). Each partition has headers and each uncompressed file stays under 1 GB. Every run writes a **manifest.json** (the doc heading writes `_manifest.json`; the text calls it manifest.json) listing partitions.
  - Manifest fields: `manifestVersion`, `byteCount`, `blobCount`, `dataRowCount`, `exportConfig {exportName, resourceId, dataVersion, apiVersion, type, timeFrame, granularity}`, `deliveryConfig {partitionData, dataOverwriteBehavior, fileFormat, compressionMode, containerUri, rootFolderPath}`, `runInfo {executionType, submittedTime, runId, startDate, endDate}`, `blobs[] {blobName, byteCount, dataRowCount}`.
- Folder layout: `<Container>/<Directory>/<ExportName>/[YYYYMMDD-YYYYMMDD]/[RunID]/` containing part files and the manifest. The example manifest shows `blobName: ".../part0.csv"`, so part files are named `part<N>.csv` (or `.csv.gz` / `.parquet`). I inferred the gzip/parquet suffixes.
  - With "Overwrite data" on (default) there is one RunID folder per month (the latest). With it off there is one RunID per run.
  - Always ingest via manifest.json, not guessed names; the doc says naming conventions may change.
- A daily export runs twice daily for the first five days of a month (a hidden second run refreshes the prior month).

### Is 'Cost and usage details (actual)' available for pay-as-you-go?
[OFFICIAL, ambiguous]
- The "supported agreement" table in the exports tutorial lists EA, MCA (all forms) and Azure internal for Cost and usage (actual). It does **not** list pay-as-you-go/MOSA.
- But the dataset schema index lists "Cost and usage details | Pay-as-you-go (MOSA) | 2019-11-01" (https://learn.microsoft.com/en-us/azure/cost-management-billing/dataset-schema/schema-index), and the exports page's Limitations says "Azure MOSP billing scopes and subscriptions don't support FOCUS datasets", implying other datasets work.
- Practical read [INFERRED]: Actual cost exports work for MOSP subscriptions, but FOCUS does not. Confirm in the portal (Cost Management > Exports > Create) before committing the plan.

### Column names
[OFFICIAL] https://learn.microsoft.com/en-us/azure/cost-management-billing/dataset-schema/cost-usage-details-pay-as-you-go (updated 2025-09-26): the **MOSA 2019-11-01 schema**, 24 columns:
`SubscriptionGuid, ResourceGroup, ResourceLocation, UsageDateTime, MeterCategory, MeterSubCategory, MeterId, MeterName, MeterRegion, UsageQuantity, ResourceRate, PreTaxCost, ConsumedService, ResourceType, InstanceId, Tags, OfferId, AdditionalInfo, ServiceInfo1, ServiceInfo2, ServiceName, ServiceTier, Currency, UnitOfMeasure`
- Mapping to the fields you asked about:
  - date = **UsageDateTime** (yyyy-mm-dd)
  - resource ID = **InstanceId**
  - resource group = **ResourceGroup**
  - meter ID = **MeterId**
  - meter name = **MeterName**
  - cost in billing currency = **PreTaxCost** (the doc says "See BillingCurrency" for `Currency`, but there is no `BillingCurrency` column)
  - billing currency = **Currency**
  - tags = **Tags**
- There is no `Date`, `ResourceId`, `CostInBillingCurrency` or `BillingCurrencyCode` in that schema. Those are the EA/MCA 2020+ and Cost Details API names.
- Column names depend on the export's dataset version and on when the export was created. A 2021 Microsoft Q&A answer says editing an export in the portal upgrades it to the latest version (https://learn.microsoft.com/en-us/answers/questions/330914/azure-billing-export-csv-format-differences) [COMMUNITY/old].
- A newer export (API 2023-05-01 `dataVersion`, or one created through the portal in 2025-26) may emit different names for MOSA. [NOT VERIFIED]
- Plan implication: make the ingest layer schema-tolerant (alias map for UsageDateTime/Date, InstanceId/ResourceId, PreTaxCost/CostInBillingCurrency, Currency/BillingCurrencyCode), and validate against a real sample export header.
- Tags format: for pay-as-you-go a JSON object string like `{"key":"value", "key2":"value2"}`; for EA the same pairs without the outer braces (`"key":"value", ...`). Source is the 2021 Microsoft Q&A thread above [COMMUNITY/old]. The official schema page just says "Tags assigned to the resource. Doesn't include resource group tags."
- Tag facts from https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/understand-cost-mgt-data: tags must be on the resource itself, appear only while applied (no back-fill), and only for resources in resource groups. Resource-group tags are not inherited unless tag inheritance is enabled.
- Cost rounding: file values are not rounded (Query API shows 8 decimals).

### Storage account with public network access disabled
[OFFICIAL] tutorial-improved-exports and https://learn.microsoft.com/en-us/azure/storage/common/storage-network-security-trusted-azure-services
- Documented path is a **firewall-enabled** account with "Allow trusted Azure services access" turned on.
  - Cost Management creates a **system-assigned managed identity** for the export.
  - It assigns that identity **Storage Blob Data Contributor** scoped to the container.
  - Trusted-service entry: resource provider `Microsoft.CostManagementExports`, listed under "trusted access based on a managed identity".
  - Same-tenant only; firewalled accounts are not supported for cross-tenant exports.
- Creating or editing the export needs **Owner** on the storage account (or a custom role with `Microsoft.Authorization/roleAssignments/write` and `Microsoft.Authorization/permissions/read`). After creation Owner is no longer needed. Updating or deleting the export removes the role assignment if you hold `roleAssignments/delete`.
- Also required: storage setting "Permitted scope for copy operations (preview)" = "From any storage account". Don't use a container that is the destination of an object-replication rule. The account must be blob or file storage.
- The docs never say the account may have "Public network access: Disabled". They describe the "selected networks + trusted services" firewall mode. A community answer I saw says trusted-service exceptions can stay in effect even when public access is set to Disabled [COMMUNITY]; not confirmed in official Storage docs.
- Safest plan: public access "Enabled from selected networks" with the trusted-services exception, and test the export. If you require strictly Disabled, expect to test it and keep a fallback.
- Fabric reading the storage later: a Blob/ADLS shortcut with workspace identity over a firewalled account needs trusted workspace access, which needs an F SKU, so it will **not work on the trial** (section 7). On trial the account must allow Fabric's access path some other way, or you must copy the data in with SAS/key/account-based auth.

---------------------------------------------------------------------
## Source list
- Trial: https://learn.microsoft.com/en-us/fabric/fundamentals/fabric-trial, https://learn.microsoft.com/en-us/fabric/enterprise/licenses, https://learn.microsoft.com/en-us/fabric/enterprise/fabric-features, https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app-install
- Tenant settings: https://learn.microsoft.com/en-us/fabric/admin/service-admin-portal-developer, https://learn.microsoft.com/en-us/fabric/admin/git-integration-admin-settings
- Git: https://learn.microsoft.com/en-us/fabric/cicd/git-integration/intro-to-git-integration, git-get-started, git-integration-process, git-automation, https://learn.microsoft.com/en-us/rest/api/fabric/core/git/connect, notebook/lakehouse/environment git pages under /fabric/data-engineering/
- fabric-cicd: https://microsoft.github.io/fabric-cicd/latest/ (how_to/parameterization, how_to/optional_feature, reference/item_types, example/authentication, example/release_pipeline, how_to/troubleshooting), https://github.com/microsoft/fabric-cicd, https://pypi.org/project/fabric-cicd/
- Terraform: https://registry.terraform.io/providers/microsoft/fabric, https://github.com/microsoft/terraform-provider-fabric (docs/ and releases)
- Environment/Runtime: /fabric/data-engineering/runtime-2-0, library-management, environment-manage-library, environment-public-api, create-and-use-environment, https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/environment-definition
- Shortcuts/identity: /fabric/onelake/onelake-shortcuts, create-adls-shortcut, shortcuts/create-blob-shortcut, https://learn.microsoft.com/en-us/rest/api/fabric/core/onelake-shortcuts/create-shortcut, /fabric/security/workspace-identity and workspace-identity-authenticate
- Notebooks/pipelines: /fabric/data-engineering/notebookutils/*, lakehouse-schemas, /fabric/onelake/onelake-access-api, /fabric/data-factory/notebook-activity, activity-overview, activity-retries, pipeline-runs, expression-language
- Cost Management: /azure/cost-management-billing/costs/tutorial-improved-exports, understand-cost-mgt-data, ingest-azure-usage-at-scale, dataset-schema/schema-index and cost-usage-details-pay-as-you-go, automate/tutorial-seed-historical-cost-dataset-exports-api, https://learn.microsoft.com/en-us/rest/api/cost-management/exports/create-or-update, https://learn.microsoft.com/en-us/azure/storage/common/storage-network-security-trusted-azure-services
