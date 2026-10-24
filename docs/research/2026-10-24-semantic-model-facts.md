# Direct Lake semantic model, RLS and report: research findings (as of 24 Oct 2026)

Method: fetched Microsoft Learn pages (live, read on 24 Oct 2026), microsoft.github.io/fabric-cicd, the fabric-cicd source and releases on GitHub, PyPI JSON, microsoft/semantic-link-labs source, microsoft/synapse-spark-runtime, and the en-AU Power BI pricing page. Nothing was signed in to or changed. No Azure, Fabric or GitHub API calls.
Tags used (same as the 2026-10-22 file): [OFFICIAL] = Microsoft/project doc or source. [INFERRED] = my reading, not stated in a doc. [COMMUNITY] = non-official or old source. [NOT VERIFIED] = could not confirm. [RECENT] = changed or dated in 2026.
Learn dates: "updated" = the page's `updated_at` metadata; ms.date given where it differs materially.

Key dates for this project:
- Power BI individual trial started ~22 Oct 2026. If it is the usual 60 days [NOT VERIFIED: no Learn page fetched states the individual-trial length], it ends ~21 Dec 2026.
- Fabric trial capacity from ~8 Nov 2026, 60 days, ends ~7 Jan 2027.
- So there is a ~2.5-week window (~21 Dec to ~7 Jan) where the capacity is alive but the user's own Power BI rights may have lapsed. See section 3.

---------------------------------------------------------------------
## 1. Direct Lake on OneLake vs Direct Lake on SQL endpoint

Main sources:
- [OFFICIAL] https://learn.microsoft.com/en-us/fabric/fundamentals/direct-lake-overview (updated 2026-10-14; ms.date 2026-09-02)
- [OFFICIAL] https://learn.microsoft.com/en-us/fabric/fundamentals/direct-lake-how-it-works (updated 2026-09-09)
- [OFFICIAL] https://learn.microsoft.com/en-us/fabric/fundamentals/direct-lake-develop (updated 2026-09-18)
- [OFFICIAL] https://learn.microsoft.com/en-us/fabric/fundamentals/direct-lake-security-integration (updated 2026-09-09)
- [OFFICIAL] https://learn.microsoft.com/en-us/fabric/fundamentals/direct-lake-power-bi-desktop (updated 2026-04-08)
- [OFFICIAL] https://learn.microsoft.com/en-us/fabric/fundamentals/direct-lake-web-modeling (updated 2026-09-18)
- Old URLs `/power-bi/enterprise/directlake-overview`, `/fabric/fundamentals/direct-lake-manage` and `/direct-lake-fixed-identity` now redirect to the overview or the security-integration page.

### Status (GA or preview)
- **Direct Lake on SQL endpoint**: GA. No preview label anywhere.
- **Direct Lake on OneLake**: the current overview, develop, web-modeling and Desktop pages carry **no preview label** for the storage mode itself. Only sub-features are marked preview: calculated tables "Yes (preview)" and calculated columns "User Context only (Preview)".
  - The develop page now says it is "the recommended Direct Lake option" for new models (how-it-works page).
  - [RECENT] Older snapshots of the same overview (still in search-engine caches) said "Direct Lake on OneLake is currently in public preview". That sentence is gone from the live page.
  - [COMMUNITY] A Towards AI article (25 Jul 2026, https://pub.towardsai.net/direct-lake-on-onelake-just-hit-ga-heres-when-it-replaces-import-mode-and-when-it-doesn-t-248cf576ccc4) says it went GA at FabCon 2026. I found no Microsoft blog post confirming a GA date. [NOT VERIFIED] exact GA date.
  - [INFERRED] The old tenant switch "Users can create Direct Lake on OneLake semantic models (preview)" is no longer in the tenant-settings index (https://learn.microsoft.com/en-us/fabric/admin/tenant-settings-index, updated 2026-09-29). The Desktop page no longer mentions a preview-feature checkbox. Check the admin portal anyway.
  - Separate and still preview: **Power BI Project (PBIP) remote modeling for Direct Lake** (page title "...in Power BI Project (preview)", https://learn.microsoft.com/en-us/fabric/fundamentals/direct-lake-power-bi-project, updated 2026-04-08).

### When each falls back to DirectQuery
- **On OneLake: never.** "Direct Lake on OneLake runs exclusively in DirectLakeOnly mode and doesn't support DirectQuery fallback." Instead of falling back it **errors**:
  - If guardrails are exceeded: "refresh fails and the model cannot be queried until the Delta tables are optimized to fall within guardrail limits."
  - Tables based on non-materialized SQL views are not allowed at all (use a materialized lake view, or Import).
  - Unframed tables: "Queries involving unprocessed tables return an error."
- **On SQL endpoint: falls back** (when `DirectLakeBehavior` = Automatic, the default) when any of these hold:
  - a table has SQL RLS, OLS/CLS (OLS/CLS actually errors) or dynamic data masking at the SQL analytics endpoint;
  - a table is a non-materialized SQL view;
  - any table exceeds a guardrail (files, row groups, rows); one table over a guardrail stops Direct Lake for the whole model;
  - the model hasn't been framed after the Delta tables were created or changed;
  - the endpoint is switched to SSO under OneLake security ("falls back to DirectQuery 100% of the time").
  - `DirectLakeBehavior` values: Automatic / DirectLakeOnly / DirectQueryOnly. It applies only to the SQL variant.
  - Diagnose with `EVALUATE TABLETRAITS()`, column `[DirectLakeFallbackInfo]`.

### Schema-enabled lakehouses
- [OFFICIAL] https://learn.microsoft.com/en-us/fabric/data-engineering/lakehouse-schemas (updated 2026-08-04): "When you build a semantic model from a schema-enabled lakehouse, you can select tables from any schema." Same-named tables in different schemas get numbered in model view.
- [OFFICIAL] The Desktop page's SQL-to-OneLake migration steps say: "If the source is a **Lakehouse without schemas**, remove all `schemaName` property references." So for a schema-enabled lakehouse, partitions keep `schemaName`.
- No limitation listed for Direct Lake on OneLake + schema lakehouses. The schema page's own limits (no workspace-level sharing of a schema lakehouse, no external ADLS table metadata) don't affect a same-workspace model. [INFERRED]

### Model-level RLS, fallback, and USERPRINCIPALNAME()
- Overview comparison table, row "Semantic model row-level security (RLS)": On OneLake "Yes – but it's strongly recommended to use a fixed identity cloud connection". The SQL variant says the same.
- Model RLS **does not cause fallback** in either variant. Fallback comes only from SQL-endpoint security. On OneLake, SQL-endpoint RLS is simply **not applied**: "queries will succeed, and SQL based RLS is not applied".
- Dynamic RLS: the RLS page (https://learn.microsoft.com/en-us/fabric/security/service-admin-row-level-security, updated 2026-07-08) gives `[UserEmail] = USERPRINCIPALNAME()` as the standard pattern and says "For Direct Lake semantic models in Microsoft Fabric, RLS is supported." Nothing restricts USERPRINCIPALNAME() for Direct Lake. In the service it returns the UPN.
- Caveats that matter for "owner sees all":
  - "semantic model data-access rules aren't enforced for users who have *Write* permission". Admin, Member and Contributor get Write implicitly. So the owner (workspace Admin) sees everything **without any rule in the access table**. RLS only bites on Viewers (including Viewers with Build).
  - The security page says to use model RLS "when report consumers aren't granted permission to query the lakehouse... and the cloud connection uses a fixed identity instead of SSO. SSO implies that end users can access the data source directly and might therefore bypass security rules in the semantic model."
    - With the default SSO, a Viewer needs Read + ReadAll on `lh_portfolio` (or a OneLake security role) for the report to work at all. With ReadAll they could read the gold tables directly in OneLake, bypassing model RLS.
  - OneLake security RLS "doesn't support dynamic definitions". So dynamic UPN filtering must live in the **model**, not OneLake security.
  - OneLake security RLS on the source blocks bidirectional relationships in the model.
  - "Service principals can't be added to an RLS role. Accordingly, RLS isn't applied for apps using a service principal as the final effective identity."

### Identity: SSO vs fixed identity
- Default is SSO (Microsoft Entra ID): every query checks the **querying user's** OneLake access (Read + ReadAll, or a OneLake security role).
- A fixed identity is a cloud connection bound to the model with SSO off. Its authentication is OAuth 2.0, Service Principal or Workspace Identity ("key or SAS ... aren't supported for Direct Lake").
- Framing always checks the **model owner's** read access: "No matter who refreshes a Direct Lake model, Direct Lake checks the owner's permission."
- Contributors and above already have Read + ReadAll in OneLake.

### TMDL shape for Direct Lake on OneLake
[OFFICIAL] facts:
- The expression uses the Azure Data Lake Storage connector, i.e. `AzureStorage.DataLake(...)`; the SQL variant uses `Sql.Database(...)` or `OneLake.SqlAnalytics()` (develop and security pages).
- Desktop migration step 6: change `Sql.Database(...)` to `AzureStorage.DataLake("https://onelake.dfs.fabric.microsoft.com/ID of the workspace/ID of the lakehouse or warehouse")`.
- Partitions have `mode` = `directLake`; `compatibilityLevel` is 1604 or higher; partitions "use shared expressions to define data sources".

[MS GITHUB, not Learn] semantic-link-labs:
- `src/sempy_labs/directlake/_generate_shared_expression.py` emits `let\n\tSource = AzureStorage.DataLake("https://onelake.dfs.fabric.microsoft.com/{workspace_id}/{item_id}")\nin\n\tSource`.
- `tom/_model.py add_entity_partition` sets `EntityName`, `SchemaName` (when given), `ExpressionSource`, `Mode = DirectLake`, and `SourceLineageTag = "[schema].[entity]"`.

[COMMUNITY] Tabular Editor's DL/SQL to DL/OneLake script adds `[HierarchicalNavigation=true]` (https://docs.tabulareditor.com/zh/features/CSharpScripts/Advanced/script-convert-dlsql-to-dlol.html).

[INFERRED] Expected TMDL, assembled from the above. I found no single Learn page showing it; confirm against a model that Fabric itself creates and commits.

```tmdl
// definition/expressions.tmdl
expression 'DirectLake - lh_portfolio' =
		let
		    Source = AzureStorage.DataLake("https://onelake.dfs.fabric.microsoft.com/<dev-workspace-guid>/<lh_portfolio-guid>", [HierarchicalNavigation=true])
		in
		    Source

// definition/tables/fact_eval_attempt.tmdl (partition part)
	partition fact_eval_attempt = entity
		mode: directLake
		source
			entityName: fact_eval_attempt
			schemaName: gold
			expressionSource: 'DirectLake - lh_portfolio'
```
- Entity names for schema tables are the **bare table name** plus `schemaName: gold`, not `gold.fact_eval_attempt`.
- The table's `sourceLineageTag` is `[gold].[fact_eval_attempt]`.
- Fabric-generated expression names vary (for example "DatabaseQuery" or "DirectLake - <item>"). Keep whatever Fabric writes.

### Other Direct Lake limits that touch this design
- Same region: a Direct Lake model can't be created in a workspace in a different region from the lakehouse.
- **Deployment pipeline rules to rebind a data source: "Not supported" for Direct Lake on OneLake.** Not relevant if you deploy with fabric-cicd, but it rules out Fabric deployment pipelines.
- GUID and binary semantic types are unsupported; complex Delta types are unsupported; NaN is unsupported.
- "One-side columns of relationships must contain unique values. Queries fail if duplicate values are detected." Relevant to Type 2 `dim_model_deployment`: relate on the surrogate key, never the business key.
- Not allowed in My workspace. Calculated columns are preview and "User Context only".

---------------------------------------------------------------------
## 2. Git and deploy

### Fabric git integration item support
[OFFICIAL] https://learn.microsoft.com/en-us/fabric/cicd/git-integration/intro-to-git-integration (updated 2026-10-17):
- **Semantic model**: listed with *(preview)* ("except push datasets, live connections to Analysis Services, model v1").
- **Report**: listed with *(preview)* (except AAS/SSAS-connected reports and Desktop-exported reports that depend on My workspace models).
- Both are still preview, unlike Lakehouse, Notebook and Pipeline (GA).

[OFFICIAL] https://learn.microsoft.com/en-us/fabric/cicd/git-integration/source-code-format (updated 2026-07-21): the semantic model folder holds `definition.pbism` and a `\definition` folder of **TMDL** files.

[OFFICIAL] https://learn.microsoft.com/en-us/power-bi/developer/projects/projects-report (updated 2026-09-29):
- "**PBIR is generally available and is the default report format.**"
- "Fabric Git Integration and Fabric REST APIs export report definitions using PBIR."
- "When the semantic model and report share the same workspace, Fabric Git Integration always exports definitions with a `byPath` reference."
- [RECENT] Editing a PBIR-Legacy report converts it to PBIR; Desktop releases before Sept 2026 are the only way to keep Legacy.

[OFFICIAL] security-integration page: model metadata in git exposes the names of secured tables and columns to anyone with repo access.

### fabric-cicd 1.4.0 and SemanticModel/Report
[OFFICIAL] PyPI JSON (https://pypi.org/pypi/fabric-cicd/json): latest **1.4.0**, uploaded 2026-10-07. Previous: 1.3.0 (2026-08-10), 1.2.0 (2026-06-30), 1.1.0 (2026-05-27).

[OFFICIAL] https://microsoft.github.io/fabric-cicd/latest/: SemanticModel and Report are both supported item types.

[OFFICIAL] https://microsoft.github.io/fabric-cicd/latest/reference/item_types/:
- SemanticModel: "Semantic Models connected to sources outside of the same workspace always point to the original item" unless parameterized. Same-workspace sources "may or may not" be re-pointed, so test.
- Without `semantic_model_binding` the first deploy "requires manual configuration of the connection after deployment". "Only a single connection binding per Semantic Model is currently supported."
- Report: `byPath` to a model in the repo is auto-converted to `byConnection` and re-pointed to the target workspace's model.

[OFFICIAL] fabric-cicd source (https://github.com/microsoft/fabric-cicd/blob/main/src/fabric_cicd/_items/_semanticmodel.py):
- Publish = item definition create/update; `allowPurgeData` is sent only with a feature flag.
- **No refresh call, no takeover, no Direct Lake-specific handling.**
- After all items publish, `semantic_model_binding` runs:
  - lists `GET /v1/workspaces/{ws}/items/{model}/connections`, and **skips the model if it has no existing connections**;
  - then calls `POST /v1/workspaces/{ws}/semanticModels/{id}/bindConnection` once per connection ID.
- Releases: 1.4.0 "Fix Semantic Model connection binding for OneLake paths missing a trailing `/`"; 1.2.0 "multiple connection ids in semantic model binding".

[OFFICIAL] https://learn.microsoft.com/en-us/rest/api/fabric/semanticmodel/items/bind-semantic-model-connection (updated 2026-09-15):
- "The caller must be the owner of the semantic model."
- SPs and managed identities are supported.
- `connectivityType` includes ShareableCloud and `Automatic` ("only available for specific scenarios like semantic models that use Single Sign-On").

### How to rebind a Direct Lake on OneLake model to the target lakehouse
- **`semantic_model_binding` does NOT rebind the source.** It binds the model's existing data-source reference to a Fabric *connection* (credentials or fixed identity). The OneLake URL in the expression is unchanged.
- **The expression must be rewritten by `find_replace`, using `$items` / `$workspace` tokens.**
  - [OFFICIAL] parameterization page (https://microsoft.github.io/fabric-cicd/latest/how_to/parameterization/): `$items.<type>.<name>.$id` works for all item types; `$workspace.$id` gives the target workspace ID.
  - Its closest worked example is a GraphModel `dataSources.json` with a OneLake `abfss://<ws>@onelake...` path. It replaces the workspace GUID with `$workspace.$id` and the lakehouse GUID with `$items.Lakehouse.Sales_Lakehouse.$id`. Same technique here.
  - Dynamic variables can't be embedded inside a larger string in `key_value_replace`. Use regex `find_replace` with a capture group, as `fabric/parameter.yml` already does for notebooks.
  - [INFERRED] Suggested entries (two regexes on the `AzureStorage.DataLake("https://onelake.dfs.fabric.microsoft.com/<ws>/<lh>"` URL):

```yaml
    - find_value: 'onelake\.dfs\.fabric\.microsoft\.com/([0-9a-fA-F-]{36})/[0-9a-fA-F-]{36}'
      replace_value: { PROD: "$workspace.$id" }
      is_regex: "true"
      item_type: "SemanticModel"
      file_path: "**/definition/expressions.tmdl"
    - find_value: 'onelake\.dfs\.fabric\.microsoft\.com/[0-9a-fA-F-]{36}/([0-9a-fA-F-]{36})'
      replace_value: { PROD: "$items.Lakehouse.lh_portfolio.$id" }
      is_regex: "true"
      item_type: "SemanticModel"
      file_path: "**/definition/expressions.tmdl"
```
  - [INFERRED] Fabric git does **not** swap the dev GUIDs in a semantic model expression for logical IDs (it does for notebooks). So git will hold the dev workspace and dev lakehouse GUIDs. Verify after the first commit.
  - `$items` resolves only items in the repo; `lh_portfolio` is in the repo, so fine.
- Report: nothing needed if `definition.pbir` is `byPath` (fabric-cicd converts it).

### Direct Lake models deployed by a service principal: known limits
- **Ownership**: an SP-created model is owned by the SP. [INFERRED from Fabric item-create semantics] The deployment-pipelines page says, for comparison, "The deploying user automatically becomes the owner" on first deploy (https://learn.microsoft.com/en-us/fabric/cicd/deployment-pipelines/understand-the-deployment-process, updated 2026-09-29).
  - Framing checks the **owner's** OneLake access. The SP must therefore keep Contributor+ on the workspace holding `lh_portfolio` (it has this already).
  - bindConnection also requires the owner, i.e. the SP. Good for fabric-cicd.
  - But "Manage the semantic model, including refresh settings – Requires semantic model ownership". So you, as a human, can't change refresh settings without taking over. Takeover makes you owner, and the next SP deploy then can't bind. [INFERRED]
- **Credentials**: "Metadata specified through the XMLA endpoint can create connections to data sources, but cannot set data source credentials" (https://learn.microsoft.com/en-us/fabric/enterprise/powerbi/service-premium-connect-tools, updated 2026-09-29).
  - For Direct Lake the default SSO usually needs no credential.
  - [NOT VERIFIED] Whether an SP-created Direct Lake on OneLake model frames successfully with no bound connection. The fabric-cicd docs say the first deploy needs manual connection setup unless you use `semantic_model_binding`.
  - [COMMUNITY] Forum threads report SP-deployed Direct Lake models still pointing at the source stage, plus credential prompts (https://community.fabric.microsoft.com/t5/Data-Warehouse/Deployment-Pipeline-does-not-rebind-Direct-Lake-semantic-model/td-p/5189473).
- **Refresh/frame after deploy**: new Direct Lake tables "are initially in an unprocessed state until the application sends a refresh command"; on OneLake, "Queries involving unprocessed tables return an error".
  - fabric-cicd sends no refresh. [INFERRED] Add a post-deploy refresh step, e.g. Power BI REST `POST .../datasets/{id}/refreshes` as the SP-owner, or a notebook.
  - "Keep your Direct Lake data up to date" is on by default. It is suspended after a non-recoverable error and resumes after a successful on-demand refresh.
- **Fixed identity / SSO**: SP *profiles* are "Not supported". Publish to web or embedding with an SP needs a fixed identity.
  - "Power BI Direct Lake semantic models can be created and queried by Service Principals" (overview limitations table).
- **Service principals and RLS**: SPs can't be role members, and "Service Principals do not work with RLS and OLS" (XMLA page). See sections 4 and 5.

---------------------------------------------------------------------
## 3. Licensing on a trial capacity

Main sources:
- [OFFICIAL] https://learn.microsoft.com/en-us/fabric/enterprise/licenses (updated 2026-08-05; ms.date 2026-06-15)
- [OFFICIAL] https://learn.microsoft.com/en-us/fabric/fundamentals/fabric-trial (updated 2026-09-29)
- [OFFICIAL] https://learn.microsoft.com/en-us/power-bi/fundamentals/service-features-license-type (updated 2026-08-31)
- [OFFICIAL] https://learn.microsoft.com/en-us/power-bi/fundamentals/service-self-service-sign-up-help (updated 2026-08-31)

### Create, edit and view on a Fabric trial capacity
- **Create** a semantic model or report (Power BI items) in a non-My-workspace workspace: needs **Pro, PPU or the Power BI individual trial**. This holds on any capacity, trial included.
  - Licences table: "Create, update, delete, or manage Power BI items in workspaces other than their 'My workspace'": Free ❌.
  - The capacity section adds "To create Power BI items in workspaces that aren't *My workspace*, you need a *Pro* license."
  - Direct Lake also needs a capacity: "Fabric capacity subscription (SKUs) only".
- **Edit** (web modelling, Desktop live edit, editing a report): same as create, because Free users can't "update ... Power BI items" outside My workspace.
  - Desktop live edit additionally says "This feature is unavailable for users with a free license."
  - Web modelling needs Write on the model.
- **View**: on the trial capacity a **Free** user can view Power BI content **with the workspace Viewer role**.
  - Scenario table: "Trial Fabric capacity ... Yes (acts like F64 for viewing)".
  - On F SKUs below F64 a Free user cannot view.
  - [INFERRED from the P-SKU row: "If you have any other role on the workspace, you need a Pro or a PPU license, or a Power BI individual trial"] A Free user who is **Admin/Member/Contributor** may also be blocked from viewing. The Free-viewing allowance is tied to the Viewer role.
- [NOT VERIFIED] Whether a trial that comes up as **F4** still "acts like F64 for viewing". The licences page says Trial = 64 CUs; the trial page says F4 or F64; the Premium page footnote says trial capacities "inherit the limits of the assigned SKU". If the trial is F4, plan as if Free viewers can't view, and increase it to 64 CUs if eligible.

### Service principal deploying via REST
- [INFERRED] No licence. SPs can't hold a Power BI licence.
  - The XMLA terms of use require a PPU licence for "the user account or app identity ... unless the semantic model resides on a Premium capacity". The model sits on a capacity.
  - The SP needs: the tenant setting "Service principals can call Fabric public APIs"; the workspace role (Member/Admin for Power BI operations per https://learn.microsoft.com/en-us/fabric/enterprise/powerbi/service-premium-service-principal, updated 2026-09-29); and a capacity.
  - SPs can't use My workspace.

### When the individual trial ends but the Fabric trial is still active
- [OFFICIAL] self-service FAQ: "When a nonextendable trial ends ... Your license reverts to the previous state (for example, back to Fabric (Free) ...)" and "You need a Pro or PPU license (or an active Fabric capacity trial) to regain those capabilities."
  - It also says "A Fabric capacity trial grants elevated capabilities similar to PPU while active; your underlying per-user license can still display as Fabric (Free)."
- [OFFICIAL] trial page: the Fabric trial "is complemented with a Power BI Individual Trial if you don't already have a Power BI Premium Per User license". And "Eligibility for another trial isn't guaranteed".
- Net reading:
  - **Optimistic** [INFERRED]: starting the Fabric trial on ~8 Nov, as its capacity admin, keeps PPU-like rights until ~7 Jan.
  - **Pessimistic** [INFERRED]: the 22 Oct individual trial is "the" complementary trial, ends ~21 Dec, and from then until 7 Jan you are Free. You could then **not create or edit** the model or report, and possibly not view them as workspace Admin, though a Viewer-role Free user could view.
  - Nothing the SP deploys is affected; the SP has no licence.
- [NOT VERIFIED] Which applies. Check Account manager > licence and trial end date right after starting the Fabric trial.

### Cheaper path than Pro
- **Viewing**: yes. Free + Viewer role on an F64-equivalent or trial capacity costs nothing. On F2 it doesn't work (below F64).
- **Authoring**: no cheaper per-user path. PPU costs more than Pro, and Free can't create or edit Power BI items outside My workspace.
- [INFERRED] Workaround: author TMDL/PBIR as code and let the SP deploy via fabric-cicd, which needs no licence.
  - That loses web modelling, Desktop live edit and Fabric git sync from the UI, which all need a licensed human.
  - "Test as role" in the service also needs someone who can open the model's Security page (Contributor+, and a licence to use Power BI items).

### Prices (en-AU)
[OFFICIAL] https://www.microsoft.com/en-au/power-platform/products/power-bi/pricing (read 24 Oct 2026; marketing page, no updated date):
- **Power BI Pro AU$21.00 user/month, paid yearly**.
- **Premium Per User AU$35.90 user/month, paid yearly**; the Pro-to-PPU add-on is AU$21.00.
- Free: AU$0.
- "Prices ... do not include GST."
- The page says "A Power BI Pro license is required for all Power BI Premium ('P') and Fabric Capacity ('F') SKUs" to publish content.
- [NOT VERIFIED] Month-to-month (non-annual) price in AUD.

The F2 price didn't render on https://azure.microsoft.com/en-au/pricing/details/microsoft-fabric/ (placeholder cells). Use the Azure calculator.

---------------------------------------------------------------------
## 4. RLS testing

### "Test as role" / "View as" in the service and web modelling
[OFFICIAL] https://learn.microsoft.com/en-us/fabric/security/service-admin-row-level-security (updated 2026-07-08; `/power-bi/admin/service-admin-rls` redirects here):
- Service path: semantic model ... > **Security** > role ... > **Test as role**. The header shows "**Now viewing as**", where you can pick other roles or "a specific person".
  - Only reports in the same workspace can be tested. It doesn't work for paginated reports.
  - "The Test as role/View as role feature doesn't work for DirectQuery models with single sign-on (SSO) enabled."
  - Direct Lake isn't named in that restriction. [NOT VERIFIED] Whether Test as role works on an SSO Direct Lake on OneLake model. Expect it to, but test it.
- Dynamic RLS: "The **Test as role** feature in the Power BI service uses your own identity when evaluating dynamic RLS expressions. This means `USERPRINCIPALNAME()` returns **your** UPN".
  - The same page also offers "a specific person" under Now viewing as. The page is AI-assisted and internally inconsistent; treat "specific person" as the way to change the UPN, and verify.
- **Web modelling**: [OFFICIAL] https://learn.microsoft.com/en-us/power-bi/transform-model/service-edit-data-models (updated 2026-10-14):
  - You can define roles (Manage roles, DAX editor for USERPRINCIPALNAME) and **Assign** members.
  - But the limitations list includes "**View as dialog**" as not supported in the service editor. Test via the Security page instead.
- **Desktop**: live edit of Direct Lake says "You can't validate RLS roles from Power BI Desktop. You can validate the role in the service."
- **Non-existent UPN**: no Learn page says the "specific person" picker accepts an arbitrary string.
  - [INFERRED] It's a people picker over the directory, so the user must exist.
  - XMLA impersonation is explicit: "The account specified in EffectiveUserName **must be in the tenant's Microsoft Entra ID** and must have both **Read** and **Build** permissions" (service-premium-connect-tools, updated 2026-09-29).
  - So you **can't test a made-up UPN**. Create real test member users in the personal tenant (free Entra users; Fabric Free licence; Read + Build on the model, no workspace role: a workspace Viewer can read the lakehouse through its SQL analytics endpoint, past the model's RLS; corrected at the plan-1 review, spec 4.1), or put your own UPN in the access table for a "Test as role" check.

### Programmatic testing
- **executeQueries REST** (https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/execute-queries, updated 2026-09-15):
  - `impersonatedUserName` = "The UPN of a user to be impersonated. If the model is not RLS enabled, this will be ignored."
  - Needs dataset Read + Build, the tenant setting **Dataset Execute Queries REST API**, and allows 120 queries/min/user.
  - **"Service Principals aren't supported for datasets with RLS ... or datasets with SSO enabled."** A Direct Lake model with dynamic RLS and default SSO is both. So executeQueries + impersonation must run as a **user**, not the SP.
- **XMLA** (`EffectiveUserName` or `Roles` connection properties): supported.
  - The impersonated user must exist in Entra and have Read + Build.
  - `Roles=` lets a workspace admin test a role without being a member, but USERPRINCIPALNAME() is still the caller.
- **Semantic link** (https://learn.microsoft.com/en-us/python/api/semantic-link-sempy/sempy.fabric):
  - `evaluate_dax(dataset, dax_string, workspace=None, verbose=0, num_rows=None, role=None, effective_user_name=None, use_readwrite_connection=False, credential=None)`. `role` and `effective_user_name` are mutually exclusive.
  - `evaluate_measure(...)` has **no** role or effective-identity parameter.
- [INFERRED] For an SSO Direct Lake on OneLake model, an impersonated effective user probably also needs OneLake Read + ReadAll on `lh_portfolio` (SSO checks the effective identity). Otherwise expect access errors rather than filtered rows. A fixed-identity connection avoids this.

### Limits of impersonation
- SPs can't be RLS role members. They can't be impersonated targets. executeQueries as an SP fails on RLS or SSO models. XMLA says "Service Principals do not work with RLS and OLS".
- RLS doesn't apply to Admin, Member or Contributor at all. To see filtering you must test as a Viewer (real or impersonated).

---------------------------------------------------------------------
## 5. Semantic link in a Fabric notebook

Sources:
- [OFFICIAL] https://learn.microsoft.com/en-us/fabric/data-science/semantic-link-overview (updated 2026-09-02)
- [OFFICIAL] https://learn.microsoft.com/en-us/fabric/data-science/semantic-link-service-principal-support (updated 2026-09-02)

### Interactive use
- `evaluate_dax` / `evaluate_measure` run as the signed-in user over XMLA (read). Nothing restricts Direct Lake. [INFERRED: Learn lists no Direct Lake exclusion]
- The user needs Build (XMLA) and, for SSO Direct Lake, OneLake read on the lakehouse. As workspace Admin you have both and RLS doesn't filter you. So the "DAX = gold" check run interactively by the owner compares **unfiltered** totals. That is right for owner-sees-all.

### Pipeline run under the owner's identity
- A pipeline notebook runs as the pipeline's last-modified user (2026-10-22 file, section 9).
  - In **dev** (owner edits the pipeline) that's you, so it behaves like interactive. [INFERRED]
  - In **prod**, fabric-cicd (the SP) deployed the pipeline, so the notebook runs **as the SP**.
- SP-triggered runs ("Fabric Pipelines" and Job Scheduler) use a default token service that supports only a listed subset: FabricRestClient, list_*, resolve_*, create_lakehouse, run_notebook_job and similar.
  - **`evaluate_dax` and `evaluate_measure` are NOT in that list.**
- Manual SP authentication (`set_service_principal` or `credential=`) unlocks more, but "The following functions aren't supported with service principal authentication: ... `sempy.fabric.evaluate_measure` ... `read_table(..., mode='rest')` ... `execute_tmsl`".
- SP support needs semantic-link ≥ 0.12.0. "Service principal authentication blocks access to the personal 'My workspace'".
- [COMMUNITY] A forum thread reports `evaluate_dax` returning 401 when the pipeline was created by an SP (https://community.fabric.microsoft.com/t5/Fabric-platform/fabric-evaluate-dax-when-run-in-context-of-SPN-fails/m-p/4621390).
- Combined with the SP and RLS limits in section 4: **in prod, use `evaluate_dax` with a manual SP credential (not `evaluate_measure`) and expect RLS/SSO problems, or run the check under a user identity.**
  - [NOT VERIFIED] Whether the notebook activity's "Connection" setting can make a SP-deployed pipeline run the notebook as the owner.

### Preinstalled in Runtime 2.0?
- Learn: "For Fabric Runtime 1.2 (Spark 3.4) and above, semantic link is available in the default runtime".
- [OFFICIAL GitHub] https://github.com/microsoft/synapse-spark-runtime, `Fabric/Runtime 2.0 (Spark 4.1)/Fabric-Python313-CPU.yml` and `Official-Spark4.1-default-2026-09-14.3-rc.1.md`: **`semantic-link-sempy==0.14.1`** (also `fabric-analytics-sdk 0.0.3.post8`). That is above the 0.12.0 needed for SP support.
- [RECENT] Runtime 2.0 page (https://learn.microsoft.com/en-us/fabric/data-engineering/runtime-2-0, updated 2026-10-23; ms.date 2026-10-22): Runtime 2.0 is now "the latest generally available (GA) and **default** runtime". New workspaces and environments use it. This settles the open question from the 22 Oct file.
- semantic-link-labs is **not** in that list [INFERRED from the grep]. `%pip install semantic-link-labs` is inline, which is disabled by default in pipeline runs (22 Oct file, section 6), so put it in the Environment if needed.

---------------------------------------------------------------------
## 6. Report authoring

### Web authoring and git
- Yes. Create the report in the browser on the dev workspace's model, then commit through git integration. Report git support is still *(preview)* (section 2).
- Commit format is PBIR ("Fabric Git Integration ... export report definitions using PBIR"), with `definition.pbir` `byPath` to the same-workspace model.
- A licence is needed (Pro, PPU or trial).
  - Service editor note: "if you're a free user and the semantic model resides in a Premium-capacity or Fabric F64 or greater workspace, the new report is saved in your *My workspace*". My workspace is not git-connected.
- Git commit needs Contributor+ and branch policy allowing direct commit (22 Oct file, section 3).

### Power BI Desktop (free) against Direct Lake on OneLake
- **Live edit of the model: no.** Desktop page "Requirements and permissions": "This feature is unavailable for users with a free license."
  - It also needs XMLA endpoint enabled on the tenant and **XMLA Read Write on the capacity**, Write on the model, and Viewer on the lakehouse.
  - [INFERRED] The default XMLA setting is read-only ("By default, read-only connectivity ... is enabled"). As trial capacity admin you must switch it to Read Write.
- **Live-connected report**: Desktop can build a report against the model with Build permission. A Free user can't publish it to the workspace (only My workspace).
  - PBIP with a Direct Lake model can't be published from Desktop at all: "You can't publish the Power BI Project (PBIP) from Power BI Desktop. You can use ... Fabric Git Integration or Fabric Item APIs."
- TMDL view and DAX query view work in live edit.

---------------------------------------------------------------------
## 7. Capacity cost and F2 limits

### Guardrails
[OFFICIAL] Direct Lake overview guardrail table, and https://learn.microsoft.com/en-us/fabric/enterprise/powerbi/service-premium-what-is (updated 2026-09-29):

| SKU | Parquet files / table | Row groups / table | Rows / table | Max model size on OneLake | Max memory | Max memory per query |
|---|---|---|---|---|---|---|
| F2 / F4 / F8 | 1,000 | 1,000 | 300 M | 10 GB | 3 GB | 1 GB |
| F64 / trial-as-F64 | 5,000 | 5,000 | 1,500 M | unlimited | 25 GB | 10 GB |

- Trial capacities "inherit the limits of the assigned SKU", so an F4 trial gets the F2/F4 row.
- **The only guardrail that can bite at ~2,000 rows is "Parquet files per table: 1,000" (F2/F4).** Daily appends can pile up small files, and on Direct Lake on OneLake breaching it makes **refresh fail and the model unqueryable** (no fallback).
  - [INFERRED] Run `OPTIMIZE` (and `VACUUM`) on gold tables in the daily load. Keep V-Order on.
- Don't enable Delta 4.x experimental features on Runtime 2.0 tables Direct Lake must read ("only work on Spark experiences").

### CU use
[OFFICIAL] https://learn.microsoft.com/en-us/fabric/enterprise/fabric-operations (updated 2026-09-29):
- Report visuals: "Interactive query" (interactive). Framing/refresh: "Semantic model on-demand/scheduled refresh" (background). Web modelling: "Web modeling read/write" (interactive). sempy/DAX Studio: "XMLA read" (interactive).
- All are on the "Power BI Capacity Usage CU" meter; "Eight CUs are equivalent to one Power BI v-core".

[OFFICIAL] https://learn.microsoft.com/en-us/fabric/enterprise/throttling (updated 2026-08-18):
- Interactive work is smoothed over at least 5 minutes (up to 64); background work over 24 hours.
- Throttling starts when 10 minutes of future capacity is used up.

[INFERRED] Rough numbers:
- A 9-table, ~2,000-row Direct Lake model is a few MB in memory.
- A report page of ~10 visuals costs on the order of a few CU-seconds per open; framing about a second or two of CU.
- On F2 (2 CU = 7,200 CU-s/hour) that is negligible next to Spark notebook runs, which dominate cost.
- Microsoft publishes no per-query CU rate for Direct Lake. Measure with the Capacity Metrics app (filter item = semantic model).
- A trial also has no autoscale.

---------------------------------------------------------------------
## Decisions this forces

- **Use Direct Lake on OneLake (approach A). Nothing found blocks it.** No preview label on Learn; it works with a schema lakehouse (`schemaName: gold`); model RLS with USERPRINCIPALNAME() is supported without fallback. Accept that it **errors instead of falling back**, so the daily load must keep gold tables framed and under 1,000 Parquet files per table (OPTIMIZE in `pl_daily_load`).
- **Rebind with `find_replace` on `definition/expressions.tmdl`**, using `$workspace.$id` and `$items.Lakehouse.lh_portfolio.$id` (regex capture groups). `semantic_model_binding` only attaches a connection, and deployment-pipeline rules don't support Direct Lake on OneLake. Verify after the first commit that git holds raw dev GUIDs in the expression.
- **Add a post-deploy refresh (frame) step** after fabric-cicd for the semantic model; fabric-cicd 1.4.0 sends none. Leave the SP as model owner, so it can bind and refresh; don't take over in prod.
- **Decide SSO vs fixed identity now.**
  - With default SSO, every Viewer needs Read + ReadAll on `lh_portfolio`, which lets them bypass model RLS through OneLake.
  - For real RLS, bind a fixed-identity cloud connection (SP or workspace identity, SSO off) through `semantic_model_binding`, and give the leads only model Read (+Build for tests), never a workspace role.
- **The owner sees all for free** as workspace Admin (RLS isn't enforced for Write). The access table maps only the leads' UPNs to projects; no "all" row is needed for the owner.
- **Test RLS with real Entra member users** (free, Read + Build on the model, no workspace role): "Test as role > Now viewing as" in the service, or `evaluate_dax(effective_user_name=...)` / executeQueries `impersonatedUserName` as a user. Made-up UPNs and SPs can't be impersonated or added to roles. Web modelling has no View as.
- **Run the "DAX = gold" notebook check under a user identity** (interactive or a dev pipeline you last edited). Use `evaluate_dax`, not `evaluate_measure`. In prod the SP-deployed pipeline runs the notebook as the SP: `evaluate_dax` is unsupported by the default SP token flow, `evaluate_measure` is unsupported for SPs, and RLS/SSO models reject SP executeQueries.
- **Finish authoring by ~20 Dec 2026, or budget Power BI Pro (AU$21/user/month + GST, annual).** Creating or editing the model and report needs Pro/PPU/trial even on the trial capacity, and the individual trial may lapse ~21 Dec, before the Fabric trial ends ~7 Jan. Check the licence state in Account manager right after starting the Fabric trial.
- **Set the trial capacity's XMLA endpoint to Read Write** if Desktop live edit or TMDL view is wanted. Desktop is not an option on a Free licence.
- **Expect preview friction in git**: semantic model and report git sync are still preview. Reports commit as PBIR (`byPath`), and the dev branch must allow direct commits.

---------------------------------------------------------------------
## Source list
- Direct Lake: /fabric/fundamentals/direct-lake-overview, direct-lake-how-it-works, direct-lake-develop, direct-lake-security-integration, direct-lake-power-bi-desktop, direct-lake-web-modeling, direct-lake-power-bi-project; /fabric/data-engineering/lakehouse-schemas
- Git/format: /fabric/cicd/git-integration/intro-to-git-integration, source-code-format; /power-bi/developer/projects/projects-report; /fabric/cicd/deployment-pipelines/understand-the-deployment-process; /rest/api/fabric/articles/item-management/definitions/semantic-model-definition (updated 2026-03-18)
- fabric-cicd: https://microsoft.github.io/fabric-cicd/latest/ (how_to/parameterization, reference/item_types), https://github.com/microsoft/fabric-cicd (src/fabric_cicd/_items/_semanticmodel.py, releases), https://pypi.org/pypi/fabric-cicd/json
- REST: /rest/api/fabric/semanticmodel/items/bind-semantic-model-connection; /rest/api/power-bi/datasets/execute-queries
- Licensing: /fabric/enterprise/licenses; /fabric/fundamentals/fabric-trial; /power-bi/fundamentals/service-features-license-type, service-self-service-signup-purchase-for-power-bi (updated 2026-03-24), service-self-service-sign-up-help; https://www.microsoft.com/en-au/power-platform/products/power-bi/pricing
- Security/XMLA/SP: /fabric/security/service-admin-row-level-security; /power-bi/transform-model/service-edit-data-models; /fabric/enterprise/powerbi/service-premium-connect-tools, service-premium-service-principal, service-premium-what-is; /power-bi/developer/embedded/embed-service-principal (updated 2026-05-12); /fabric/admin/tenant-settings-index
- Semantic link: /fabric/data-science/semantic-link-overview, semantic-link-service-principal-support; /python/api/semantic-link-sempy/sempy.fabric; https://github.com/microsoft/semantic-link-labs; https://github.com/microsoft/synapse-spark-runtime (Fabric/Runtime 2.0 (Spark 4.1)); /fabric/data-engineering/runtime-2-0
- Capacity: /fabric/enterprise/fabric-operations, /fabric/enterprise/throttling
- Community: Towards AI (GA claim), docs.tabulareditor.com (DL/SQL to DL/OneLake script), community.fabric.microsoft.com threads cited inline
