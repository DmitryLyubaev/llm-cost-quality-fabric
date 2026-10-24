# Fabric data agent + Foundry "portfolio analyst": research findings (as of 25 Oct 2026)

Method: fetched Microsoft Learn pages (live, read on 25 Oct 2026), the Fabric REST API reference, microsoft.github.io/fabric-cicd (item types, changelog), the Azure Retail Prices API (public, unauthenticated), nuget.org and PyPI package metadata, and the en-AU Foundry Agent Service pricing page. Read project 3's README, `Directory.Packages.props`, `src/Workshop.Agent/Engines/Foundry/*.cs`, and reflected over its built SDK assemblies (`bin/Release/net10.0`). Nothing was signed in to or changed. No Azure, Fabric or GitHub API calls.
Tags (same as earlier files): [OFFICIAL] = Microsoft/project doc or source. [INFERRED] = my reading, not stated in a doc. [COMMUNITY] = non-official or old source. [NOT VERIFIED] = could not confirm. [RECENT] = changed or dated in 2026.
Learn dates: "updated" = the page's `updated_at` metadata; ms.date given where it differs materially.

Key dates for this project:
- **6 Nov 2026: the Foundry "Microsoft Fabric data agent" tool is scheduled for deprecation** (section 4). That is before the Fabric trial starts (~8 Nov).
- Fabric trial from ~8 Nov 2026. **A trial capacity does not support the data agent** (section 1). Every data agent session needs the paid F2 running.
- Power BI individual trial may lapse ~21 Dec (2026-10-24 file, section 3).

---------------------------------------------------------------------
## 1. Fabric data agent: status and capacity

Main sources:
- [OFFICIAL] https://learn.microsoft.com/en-us/fabric/data-science/concept-data-agent (updated 2026-10-21; ms.date 2026-05-11)
- [OFFICIAL] https://learn.microsoft.com/en-us/fabric/data-science/data-agent-tenant-settings (updated 2026-09-29; ms.date 2026-04-20)
- [OFFICIAL] https://learn.microsoft.com/en-us/fabric/enterprise/fabric-features (updated 2026-06-30)
- [OFFICIAL] https://learn.microsoft.com/en-us/fabric/fundamentals/copilot-fabric-overview (updated 2026-09-04)
- [OFFICIAL] https://learn.microsoft.com/en-us/fabric/fundamentals/copilot-fabric-consumption (updated 2026-07-20)
- [OFFICIAL] https://learn.microsoft.com/en-us/fabric/enterprise/fabric-operations (updated 2026-09-29)
- [OFFICIAL] https://learn.microsoft.com/en-us/fabric/enterprise/fabric-copilot-capacity (updated 2026-10-21; ms.date 2026-10-20)
- [OFFICIAL] https://learn.microsoft.com/en-us/fabric/admin/region-availability (updated 2026-10-06)

### GA or preview
- **GA.** "Data agent in Microsoft Fabric is a generally available feature" (concept page).
- Sub-features are still preview:
  - Python SDK and evaluation (section 5).
  - Source control / ALM ("Source control for Fabric data agents is currently in preview", section 3).
  - The REST "configuration management" APIs, including Publish.
  - The "preview runtime" (schema object descriptions, topics, advanced DAX generation).
  - Graph, ontology and Azure AI Search sources.

### Capacity: F2 is enough
- Every data agent page has the same prerequisite: "A paid F2 or higher Fabric capacity, or a Power BI Premium per capacity (P1 or higher) capacity with Microsoft Fabric enabled." F64 is not required.

### Trial capacity: explicitly unsupported
- Features-by-SKU page: "Unless otherwise noted, a trial capacity doesn't support any of these features." The "Fabric data agent" row (F SKUs Yes, P SKUs Yes) has **no** trial footnote. The footnote is on managed private endpoints and Free-licence viewing only.
- Copilot overview: "The Azure OpenAI Service isn't available on trial SKUs."
- The word "paid" in every prerequisite says the same.
- [NOT VERIFIED] Whether a **Fabric Copilot capacity** (a paid F2 the user is assigned to) would let a workspace on the *trial* capacity run a data agent.
  - The Copilot-capacity page lists "Data agents on a Fabric capacity workspace where the capacity SKU is smaller than F64".
  - "Trial" appears only in the Power BI Copilot licence-mode list.
  - It also requires the Copilot capacity to be "in the Fabric tenant's home region". Don't plan on it.

### Tenant settings
Data-agent tenant settings page (changes "might take up to one hour to take effect"):
1. **Users can use Copilot and other features powered by Azure OpenAI** (tenant and capacity level).
2. **Capacities can be designated as Fabric Copilot capacities** (listed as required on the page; strictly needed only to use a Copilot capacity [INFERRED]).
3. **Data sent to Azure OpenAI can be processed outside your capacity's geographic region, compliance boundary, or national cloud instance**: "Required for customers using Fabric data agent whose capacity's geographic region is outside of the EU data boundary and the US." **Australia East needs it.**
4. **Data sent to Azure OpenAI can be stored outside your capacity's geographic region...**: needed for data agents outside EU/US. Conversation history is kept "up to 28 days if not manually removed".

There is no separate "data agent" on/off switch on that page. Copilot overview: "Copilot is enabled by default for tenants with paid Fabric capacities (F2 or higher)". Check anyway.
- [INFERRED] For any SP automation (fabric-cicd, Publish REST, MCP baseline), "Service principals can call Fabric public APIs" must stay on (already on for project 9).

### CU consumption
- Fabric operations page, "Data agent in Fabric": operation **AI query**, item kind **LlmPlugin**, meter **Copilot and AI**, type **Background**.
- Rates are the Copilot rates (consumption page):
  - 100 CU-s per 1,000 input tokens;
  - 10 CU-s per 1,000 cached input tokens;
  - 400 CU-s per 1,000 output tokens.
  - Worked example: 2,000 in + 500 out = 400 CU-s.
- Background, so smoothed over 24 h.
- [INFERRED] The generated SQL/DAX runs on the source and bills there too: "SQL Endpoint Query" (Data Warehouse meter, background) for a lakehouse; "Interactive query"/"XMLA read" (Power BI meter) for a semantic model. Learn doesn't say this for the data agent specifically.
- [INFERRED] A data agent answer is several LLM calls (orchestrator + NL2SQL/NL2DAX + formatting) over schema context. Budget roughly 1,000–3,000 CU-s per question.
  - F2 = 172,800 CU-s/day. A 15-question run at ~2,000 CU-s is ~30,000 CU-s (~17% of a day).
  - Background smoothing spreads it, but **pausing bills all remaining smoothed usage at once** ("the remaining cumulative overages and smoothed operations on your capacity are summed, and added to your Azure bill", https://learn.microsoft.com/en-us/fabric/enterprise/pause-resume, updated 2026-08-14).
  - Measure in the Capacity Metrics app (item kind LlmPlugin).

### Region (Australia East)
- Region page: Australia East has "All Fabric workloads", with nothing listed as unavailable.
- The prebuilt Azure OpenAI behind Fabric Copilot/data agent runs only in US datacentres and the EU data boundary. The geography table maps **Australia to US**: "Data processing outside your capacity's geographic region? Yes", so both cross-geo settings are needed.
- **Same-region rule**: "The Fabric data agent can't execute queries when the data source's workspace capacity is in a different region than the data agent's workspace capacity." The data agent and `lh_portfolio` (and `sm_portfolio`) must sit on capacities in the same region.

---------------------------------------------------------------------
## 2. Data sources

Main sources:
- [OFFICIAL] https://learn.microsoft.com/en-us/fabric/data-science/data-agent-add-datasources (updated 2026-09-30)
- [OFFICIAL] https://learn.microsoft.com/en-us/fabric/data-science/how-to-create-data-agent (updated 2026-09-29)
- [OFFICIAL] https://learn.microsoft.com/en-us/fabric/data-science/data-agent-configurations (updated 2026-09-29)
- [OFFICIAL] https://learn.microsoft.com/en-us/fabric/data-science/data-agent-example-queries (updated 2026-06-23)
- [OFFICIAL] https://learn.microsoft.com/en-us/fabric/data-science/semantic-model-best-practices (updated 2026-09-01; the old `/data-agent-semantic-model` URL lands here)
- [OFFICIAL] https://learn.microsoft.com/en-us/fabric/data-science/data-agent-configuration-best-practices (updated 2026-08-31)
- [OFFICIAL] https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/data-agent-definition (updated 2026-02-11)

### What it can read, and in what language
| Source | Language | Notes |
|---|---|---|
| Lakehouse (tables via SQL analytics endpoint), Warehouse, SQL database, mirrored DBs | T-SQL (NL2SQL) | Example queries yes; data source instructions + description yes |
| Power BI semantic model (any storage mode) | DAX (NL2DAX, via XMLA) | **No example queries, no data source instructions/description**; configured through Prep for AI |
| Eventhouse KQL database | KQL (NL2KQL) | Example queries yes |
| Graph model (preview), Ontology (preview), Azure AI Search (preview), Microsoft Graph | GQL / mixed / search | n/a here |

- **Up to five data sources** per agent, any mix.
- Lakehouse: tables only, not files.
- **Schema-enabled lakehouse**: no Learn sentence says "supported" or "unsupported".
  - The definition schema has element types `lakehouse_tables.schema` and `warehouse_tables.schema`, and the git folder naming is per source, not per schema.
  - [INFERRED] Schemas show as a level in the Explorer tree, and you select `gold` tables under it. Write example SQL with `gold.<table>`, since the SQL endpoint exposes the schema. Verify on first build.
- **Direct Lake**: no Learn page restricts the storage mode.
  - Semantic models are queried over XMLA. The best-practices page notes Q&A "is enabled by default for Import and Direct Lake models", which advanced DAX generation needs for value indexing.
  - [INFERRED] Direct Lake on OneLake works, provided the model is framed (2026-10-24 file).

### Limits per source
- Tables: [COMMUNITY] a Fabric blog ("Expanded data agent support for large data sources"; 403 when fetched, search snippet only) says the earlier "< 1,000 tables / < 100 columns + measures" limits were lifted for Lakehouse and Warehouse. Learn lists no table cap now. Irrelevant at ~10 gold tables.
- Instructions: **15,000 characters** for agent-level instructions (how-to-create page). Topics (preview runtime) allow up to 1 million characters for SQL sources.
- Example queries:
  - **up to 100 per data source** (concept page limitations);
  - only Lakehouse/Warehouse/KQL (not semantic model or ontology);
  - each is validated against the selected schema, and invalid ones are silently not used;
  - retrieval sends the most similar: "the top three" (configurations page) vs "typically the top four" (example-queries page). Learn contradicts itself.
- Answers: "responses are capped at a maximum of 25 rows and 25 columns", and earlier turns influence later ones. Start a new chat per question.
- English only. The LLM can't be changed. All queries are read-only.

### RLS and identity
- "Fabric data agents access underlying data sources by using the requesting user's identity and permissions."
- Semantic model: "Access to Power BI semantic models through a data agent is governed by Read permission on the model and doesn't require workspace-level access. Row-Level Security (RLS) and Column-Level Security (CLS) still apply."
  - Build is not required for agent-driven queries.
- So `sm_portfolio`'s `USERPRINCIPALNAME()` RLS filters a lead (Viewer, Read only). The owner (workspace Admin, has Write) sees everything (2026-10-24 file, section 1).
- Lakehouse source: the caller needs "Read on the lakehouse item (and table access, if enforced)".
  - [INFERRED] For a SQL source this means SQL-endpoint permissions (ReadData / workspace role). Model RLS does not apply on the lakehouse path, so a lead with lakehouse Read would see all rows. Don't give leads the lakehouse source.
- Service principal through the MCP endpoint: [INFERRED from the 2026-10-24 file] "RLS isn't applied for apps using a service principal as the final effective identity", and SP executeQueries fails on RLS/SSO models. Expect SP queries against `sm_portfolio` to fail or be unfiltered. Use the lakehouse source for SP runs. [NOT VERIFIED]

### Which source is most reliable for numeric questions
- Learn gives no ranking. Relevant facts:
  - Semantic model: "the DAX generation tool used by data agent relies solely on the semantic model's metadata and Prep for AI configurations. The DAX generation tool ignores any instructions you add at the data agent level". It also has no example queries. Accuracy levers are AI data schema, verified answers, AI instructions, explicit measures and descriptions, all of which live in the model (project 11's TMDL), not the agent.
  - SQL sources: data source instructions, schema object descriptions (preview runtime), up to 100 validated example queries, and an SDK validator (`evaluate_few_shots`, SQL only) with conflict detection.
- [INFERRED] For a frozen set scored against gold numbers, the **lakehouse gold tables with curated example queries** are the most controllable and testable path.
  - The semantic model wins only where a business measure is already defined and must stay consistent with the report.
  - Keep each question answerable in 25 rows or fewer (prefer scalar answers).

### What instructions and example queries look like
- Agent instructions template (configurations page): `## Objective`, `## Data sources`, `## Key terminology`, `## Response guidelines`, `## Handling common topics`.
- Data source instructions template: `## General knowledge`, `## Table descriptions`, `## When asked about`.
- Example query = `{ "id": <guid>, "question": "...", "query": "SELECT ..." }` in `fewshots.json` (section 3). Best practices:
  - one pattern per example;
  - comments in SQL to mark substitution points;
  - no overlap or contradictions;
  - check which examples were retrieved in "run steps".

---------------------------------------------------------------------
## 3. Lifecycle (git, fabric-cicd, definition, publish)

Main sources:
- [OFFICIAL] https://learn.microsoft.com/en-us/fabric/cicd/git-integration/intro-to-git-integration (updated 2026-10-17)
- [OFFICIAL] https://learn.microsoft.com/en-us/fabric/data-science/data-agent-source-control (updated 2026-08-31; ms.date 2026-08-27)
- [OFFICIAL] data-agent definition page (above, updated 2026-02-11)
- [OFFICIAL] https://learn.microsoft.com/en-us/rest/api/fabric/dataagent/items (updated 2026-06-29), .../publish-data-agent and .../update-data-agent-definition (both updated 2026-09-15)
- [OFFICIAL] https://microsoft.github.io/fabric-cicd/latest/reference/item_types/ and /latest/changelog/

### Fabric git integration
- **Yes.** Supported-items list, Data Science: "Data Agents" with **no** *(preview)* marker (unlike semantic model and report).
- The data-agent source-control page still says "Source control for Fabric data agents is currently in preview". [INFERRED] Treat it as preview.
- Any change makes the item "Uncommitted": schema selection, AI or data source instructions, example queries, **publishing**, or the publish description.

### fabric-cicd 1.4.0
- **Yes.** "Data Agent" is a documented item type.
- Changelog:
  - v0.1.30 (20 Oct 2025) "Add support for publishing and managing Data Agent items";
  - v0.1.31 (1 Dec 2025) "Correct the publish order of Data Agent and Semantic Model items".
  - Latest is v1.4.0 (7 Oct 2026).
- Item-types page, Data Agent: "Data source items (e.g., Lakehouse, Warehouse, Semantic Model) will always reference the original item unless parameterized in the `find_replace` section of the `parameter.yml` file." Nothing else is documented.
- [NOT VERIFIED] Whether fabric-cicd's deploy makes the deployed agent *published*. "Publishing" in its changelog means deploying the item. Plan on an explicit Publish call after deploy (below).

### Definition format (files)
Git (source-control page) uses `files/config/...`; the REST definition uses `Files/Config/...`:
```
<agent>.DataAgent/
  .platform
  Files/Config/data_agent.json              {"$schema": "2.1.0"}
  Files/Config/publish_info.json            {"$schema": "1.0.0", "description": "..."}   (once published)
  Files/Config/draft/stage_config.json      {"$schema": "1.0.0", "aiInstructions": "..."}
  Files/Config/draft/lakehouse-tables-lh_portfolio/datasource.json
  Files/Config/draft/lakehouse-tables-lh_portfolio/fewshots.json
  Files/Config/draft/semantic-model-sm_portfolio/datasource.json      (no fewshots.json for models)
  Files/Config/published/...                mirror of draft, the published stage
```
- Folder prefixes: `lakehouse-tables-`, `warehouse-tables-`, `semantic-model-`, `kusto-`, `ontology-`, followed by the source's display name.
- `datasource.json`:
  - holds `artifactId`, `workspaceId` (both required), `displayName`, `type` (e.g. `lakehouse_tables`, `semantic_model`), `dataSourceInstructions`, `userDescription`;
  - `elements` is a tree with `is_selected` per table. Column-level selection is ignored.
- "Don't directly make changes to the published folder in the Git repository."

### IDs that need parameterisation
- **Yes**: every `datasource.json` holds the source's `workspaceId` and `artifactId`.
- [INFERRED] Use the same regex `find_replace` pattern as the semantic model (2026-10-24 file, section 2), scoped to `item_type: "DataAgent"`, `file_path: "**/datasource.json"`. Replace with `$workspace.$id` and `$items.Lakehouse.lh_portfolio.$id` (and `$items.SemanticModel.sm_portfolio.$id`).
- Apply it to both `draft/` and `published/` copies.
- [NOT VERIFIED] Whether Fabric git rewrites these to logical IDs (it does for notebooks). Check after the first commit.

### Publishing, and by API
- Portal: **Publish** asks for a description. Afterwards there are two versions: the draft (editable) and the published one (shared and consumable).
- "unpublished data agents are not accessible for consumption even if they are in production workspace". This covers the MCP endpoint, Foundry, the SDK's `production` stage and Copilot Studio.
- **REST**: `POST https://api.fabric.microsoft.com/v1/workspaces/{workspaceId}/dataAgents/{dataAgentId}/staging/publish`, body `{"publishedDescription": "..."}`.
  - "Data Agent configuration management is currently in Preview".
  - Scopes: `Item.ReadWrite.All` or `DataAgent.ReadWrite.All`.
  - **Service principals and managed identities: Yes.**
- SDK: `agent.publish_staging(description=...)` "works inside and outside Fabric".
- `updateDefinition` also supports SP/MI (LRO).
- SP scope conflict:
  - The source-control page (updated 2026-08-31) says "Service principals are supported in the Fabric data agent **only** as part of ALM scenarios".
  - The newer MCP and SDK pages (2026-09-04, 2026-10-14) and the REST reference say SP works for MCP queries and publish.
  - [INFERRED] The newer pages win; test SP querying early.
- Deployment pipelines also support data agents (same tenant only). Not used here (fabric-cicd).

---------------------------------------------------------------------
## 4. Foundry Agent Service: Microsoft Fabric tool

Main sources:
- [OFFICIAL] https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/fabric (updated 2026-10-14; ms.date 2026-09-14). The old `/azure/ai-foundry/...` URL now lands here.
- [OFFICIAL] https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/fabric-iq (updated 2026-10-23; ms.date 2026-09-29)
- [OFFICIAL] https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/configure-user-delegation (updated 2026-10-23)
- [OFFICIAL] https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/limits-quotas-regions (updated 2026-10-15)
- [OFFICIAL] https://learn.microsoft.com/en-us/fabric/data-science/data-agent-mcp-server (updated 2026-09-04)

### Status: the tool you planned on is being retired
- [RECENT] Fabric tool page, top banner: "**The Microsoft Fabric data agent tool in Foundry Agent Service is scheduled for deprecation on November 6, 2026. Use the Fabric IQ tool instead.**"
  - Tool type `fabric_dataagent_preview`; .NET `MicrosoftFabricPreviewTool`; still "preview".
  - [NOT VERIFIED] Whether "deprecation" means it stops working on 6 Nov or only stops being recommended. Either way it is gone or going before the capstone starts.
- **Replacement: Fabric IQ tool (preview)**, type `fabric_iq_preview`.
  - It is an MCP-based tool targeting one Fabric item: a data agent, an ontology or a semantic model.
  - Data agent endpoint: `https://api.fabric.microsoft.com/v1/mcp/workspaces/{workspaceId}/dataagents/{dataAgentId}/agent`.
  - Migration: "Replace the old `fabric_dataagent_preview` tool with the Fabric IQ tool... create a Foundry project connection to your published data agent's MCP endpoint." "The migration changes the Foundry tool integration, not the Fabric data agent".
  - "Fabric IQ isn't available in regions where Power BI is the only Fabric workload". Australia East is full Fabric, so this is fine.

### Identity
- Old tool: "identity passthrough (On-Behalf-Of)... Service principal authentication isn't supported for the Fabric data agent."
  - The same page's prerequisites also say "Use user identity authentication or service principal". It contradicts itself.
- Fabric IQ, through a Foundry connection: data agent connections use `UserEntraToken` or `AgentOnBehalfOfUser` (on-behalf-of the **signed-in user**), or `OAuth2` (in-conversation sign-in).
  - Scope: `https://analysis.windows.net/powerbi/api/DataAgent.Execute.All`. Admin consent may be needed for BYO apps.
  - "**Only a direct client of the published data-agent MCP endpoint can instead use a user or service-principal token.**"
- Data agent MCP page: "The token can represent either a user identity or a service principal... request the token for the `https://api.fabric.microsoft.com/.default` scope." Tip: "To run unattended, such as in a service or a job, use a service principal credential instead".
- **Net:**
  - **The Foundry path is user-delegated only (documented).** A scripted Foundry run must be made by a signed-in user (az login or interactive), not by the GitHub Actions OIDC SP.
  - **The direct MCP path accepts an SP**, so an unattended *baseline* (data agent alone) is possible.
  - [INFERRED] OBO needs a user token by definition. An SP caller to Foundry has no user to act for. No managed-identity option is documented for this tool.

### Tenant and region
- "Ensure your Fabric data agent and Foundry project are in the same tenant" (old tool). Both are in the personal tenant: OK.
  - The *identity* matters. The caller needs **Foundry User** on the project **and** Read on the data agent plus each source.
  - Today the fabric@ user has Fabric rights and the subscription owner has Foundry rights. One of them must get the other side [INFERRED].
  - Fabric IQ also needs "A Microsoft Fabric license ... Users who invoke Fabric IQ through your agent must also have this license."
- Region: the tool-by-region table shows East US 2 = yes for "Fabric Data Agent" and "MCP". Fabric IQ isn't a column.
  - Fabric IQ data residency: "If your Foundry project is in a different Azure region than your Fabric workspace, query results are returned cross-region." eastus2 Foundry with Australia East Fabric is allowed. Results cross to the US, which the data agent does anyway for LLM processing.
- Model: the tool-by-model table lists Fabric Data Agent and MCP support for gpt-4.1/4o/5/5.1–5.5 etc.
  - **`gpt-5.6-luna` (project 3's GPT deployment) isn't in the table.** [NOT VERIFIED]
  - Fabric IQ recommends `gpt-5.4`/`gpt-5.5` for background mode and semantic models.

### Connection setup
- Old tool: a `CustomKeys` project connection with keys `workspace_id` and `artifact_id` (from `.../groups/<workspace_id>/aiskills/<artifact_id>`), created by ARM `PUT .../projects/{p}/connections/{name}?api-version=2025-04-01-preview`.
- Fabric IQ: a `RemoteTool` connection.
  - Example (private-link variant): `{"category":"RemoteTool","authType":"UserEntraToken","target":"<data agent MCP URL>","audience":"https://analysis.windows.net/powerbi/api"}` via ARM `api-version=2025-10-01-preview`, or `azd ai connection create ... --kind remote-tool --auth-type user-entra-token`.
  - The target URL embeds the workspace ID and data agent ID, so it is per environment.
  - The portal can instead pick the data agent from the OneLake catalog.
  - Creating it needs **Foundry Project Manager**.

### APIs and SDKs; can project 3 attach it?
- Project 3 uses **Azure.AI.Projects 2.0.1** (Azure.AI.Projects.Agents + Azure.AI.Extensions.OpenAI 2.0.0, OpenAI 2.10.0), with `DeclarativeAgentDefinition` prompt agents, `AgentAdministrationClient.CreateAgentVersionAsync`, and `ProjectResponsesClient` (Responses API, agent reference).
- Reflection over its built assemblies:
  - **Has** `MicrosoftFabricPreviewTool` / `FabricDataAgentToolOptions.ProjectConnections` / `ToolProjectConnection` (the deprecated tool). On the response side it has `FabricDataAgentToolCall` (`Arguments`) and `FabricDataAgentToolCallOutput` (`Output: BinaryData`).
  - **Has** OpenAI `McpTool` plus `McpToolExtensions.Get/SetProjectConnectionId`.
  - **Does not have** `FabricIQPreviewTool`, toolbox types, or `ProjectsAgentTool.AsProjectTool` (used by the Fabric IQ .NET sample).
- NuGet: Azure.AI.Projects stable 2.0.1 (23 Apr 2026). Then 3.0.0-beta.1 (24 Aug) to **3.0.0-beta.5 (16 Oct 2026)**, which depends on Azure.AI.Projects.Agents 3.0.0-beta.5 and Azure.AI.Extensions.OpenAI 3.0.0-alpha.
  - [INFERRED] `FabricIQPreviewTool` ships only in the 3.0.0 betas. [NOT VERIFIED] which beta.
  - Upgrading also reopens project 3's OpenAI 2.10/2.11 pin problem.
- Python: Fabric IQ sample needs `azure-ai-projects>=2.2.0` (PyPI latest 2.9.0, 17 Oct 2026), class `FabricIQPreviewTool`.
  - The old tool was in `azure-ai-projects` (`MicrosoftFabricPreviewTool`) and the classic `azure-ai-agents` sample.
  - Usage tables: Python, C#, JS, REST and basic/standard setup all supported; Java for the old tool only.
- **Prompt agents / Responses API: yes.** Both tools are shown as `PromptAgentDefinition`/`DeclarativeAgentDefinition` tools called through `responses.create` with the agent reference.
- [INFERRED] **Path without an SDK upgrade**: attach a plain `mcp` tool (`McpTool` with `ProjectConnectionId`) whose connection is the `RemoteTool` + `UserEntraToken` connection to the data agent MCP URL.
  - The Fabric IQ REST sample does exactly this shape (an `mcp` tool with `project_connection_id`), but with a toolbox URL as target.
  - The delegation page lists "Fabric IQ — Fabric Data Agent" as a `UserEntraToken` target with scope `DataAgent.Execute.All`.
  - It also says "For ... a custom MCP server, use `AgentOnBehalfOfUser`, not `UserEntraToken`". Whether a bare `mcp` tool pointing straight at the data agent counts as the recognised Fabric IQ target is [NOT VERIFIED]. Spike it.

### Known limits
- Tools per agent: 128. Learn states no "one Fabric tool per agent" limit.
  - The old tool's `project_connections` is a list. The Fabric IQ portal flow says "Name each connected data agent", which implies several are allowed.
- Timeouts: no number published.
  - "Ontology and Power BI semantic model endpoints run synchronously and are subject to the standard tool-call timeout."
  - **Only the data agent MCP endpoint supports long-running operations via background mode** (`background: true`, poll; needs e.g. gpt-5.4/5.5).
  - The MCP server itself supports MCP tasks (`tasks/get`, `tasks/cancel`).
  - Old tool troubleshooting: "Increase timeout settings... retry logic with exponential backoff."
- Citations: Fabric IQ "Annotation chunks are returned in `result.structuredContent.documents[]`" (title, url).
- **Generated SQL/DAX**: no Learn page says the MCP or Foundry tool output includes the query text. The data agent "exposes a single tool" that returns an answer.
  - The Fabric UI shows run steps and generated code.
  - `evaluate_data_agent` writes a `<table>_steps` table with "detailed reasoning and step-by-step execution".
  - The Diagnostics button exports steps.
  - [NOT VERIFIED] Whether `FabricDataAgentToolCallOutput.Output` or MCP `content` carries the query. Log the raw output and inspect.
- Foundry traces show tool inputs and outputs (tool best-practice page, updated 2026-09-24).
- Use `tool_choice: "required"` for determinism.

---------------------------------------------------------------------
## 5. Evaluation without Foundry

- **Direct query, yes.** Published data agent, then the **MCP endpoint** (streamable HTTP; `initialize` → `tools/list` → `tools/call`), user or **SP** token, Fabric scope (MCP page, section 4).
  - Not a plain REST API: "A generic HTTP client that skips the handshake and message format won't work."
  - No dynamic client registration.
- **fabric-data-agent-sdk** (PyPI `fabric-data-agent-sdk`, latest **0.1.33a0**, 12 Oct 2026, still alpha; Learn page "Fabric data agent Python SDK (preview)", https://learn.microsoft.com/en-us/fabric/data-science/fabric-data-agent-sdk, updated 2026-10-14):
  - management plane (`create_data_agent`, `update_settings(ai_instructions=)`, `add_staging_datasource`, `add_fewshots`, `publish_staging`);
  - runs inside a Fabric notebook or outside with `SetFabricAnalyticsDefaultTokenCredentialsGlobally(credential)`, user or SP.
  - [RECENT] "The Fabric Data Agent SDK transitioned from the OpenAI Assistants API to the OpenAI Responses API for querying data agents". Runtime querying now goes through MCP (or the "Fabric OpenAI Responses client").
- **Microsoft guidance on ground-truth evaluation: yes.** https://learn.microsoft.com/en-us/fabric/data-science/evaluate-data-agent (updated 2026-09-01; preview):
  - `evaluate_data_agent(df, data_agent_name, workspace_name=None, table_name="evaluation_output", data_agent_stage="production"|"sandbox", critic_prompt=None)`. `df` has `question`, `expected_answer`. It returns an evaluation ID.
  - It writes `<table>` (summary) and `<table>_steps` (reasoning/steps) to the lakehouse.
  - `get_evaluation_summary(table)` gives correct/incorrect/unclear counts. `get_evaluation_details(id, table, get_all_rows, verbose)`.
  - The judge is an LLM comparing answers; override with `critic_prompt` containing `{query}`, `{expected_answer}`, `{actual_answer}`.
  - Thread links are "visible only to the user who ran the evaluation".
  - Shown in a notebook (`%pip`). [INFERRED] It runs as the notebook user.
- Example-query validator: `datasource.evaluate_few_shots(batch_size=20)` scores Clarity/Relatedness/Mapping and detects conflicts. **SQL only.**
- Best-practice pages also recommend iterating on a ground-truth set and inspecting the generated query for every failure.
- [INFERRED] Use one frozen CSV for all three runs: (a) `evaluate_data_agent` in a notebook, (b) a direct MCP client (SP or user), (c) the Foundry agent via project 3. Score with project 3's own deterministic checks (numeric tolerance), not only the LLM critic.

---------------------------------------------------------------------
## 6. Cost

- **Retail Prices API** (`https://prices.azure.com/api/retail/prices`, `currencyCode=AUD`, `serviceName eq 'Microsoft Fabric' and armRegionName eq 'australiaeast'`, read 25 Oct 2026):
  - pay-as-you-go capacity meters ("Compute Pool / Power BI / Spark / Data Warehouse / **Copilot and AI** Capacity Usage CU") are all **AUD 0.2992 per CU-hour**;
  - **F2 = 2 CU = AUD 0.5984/hour**, about AUD 437 per 730-hour month if never paused. Retail prices exclude GST [INFERRED, the API's usual basis];
  - reservation "Fabric Capacity CU" AUD 1,558.52 (1 yr) and AUD 4,675.55 (3 yr) per CU [INFERRED per CU-year]. Useless with pause/resume;
  - [RECENT] new "… OD On-Demand Usage CU" meters effective 2026-10-01 (e.g. Power BI OD AUD 0.8975, Copilot and AI OD AUD 0.2992). [NOT VERIFIED] what triggers them (probably on-demand/overage billing); check the bill.
- **Data agent extra cost**: no per-seat fee. Its LLM work bills to the capacity as "AI query" (Copilot and AI meter) at the Copilot token rates (section 1), plus the source query CUs. On F2 it competes with Spark/Power BI for the same 2 CU.
- **Foundry**: "There is no additional charge for creating or running Foundry-native agents using prompts and workflows". You pay model tokens. "You will incur separate charges and licenses for tools and knowledge connections, including ... Microsoft Fabric data agent" (https://azure.microsoft.com/en-au/pricing/details/foundry-agent-service, marketing page, no date; rates show as placeholders).
  - [INFERRED] The "separate charge" is the Fabric capacity consumption above. No Foundry-side Fabric tool meter is published.
- Pause bills accumulated smoothed usage immediately (section 1). Run evaluation early in a session and pause after a cooldown.

---------------------------------------------------------------------
## Decisions this forces

- **Don't build on the Foundry "Microsoft Fabric data agent" tool.** It is deprecated on 6 Nov 2026, before the capstone starts. Target **Fabric IQ** (`fabric_iq_preview`) or a plain **MCP tool** pointed at the published data agent's MCP endpoint, via a `RemoteTool` + `UserEntraToken` connection.
- **Project 3's SDK (Azure.AI.Projects 2.0.1) can't attach Fabric IQ.** Choose between:
  - (a) a spike with 2.0.1's `McpTool` + `ProjectConnectionId` (no upgrade; not verified to be accepted for the data agent target);
  - (b) moving to Azure.AI.Projects 3.0.0-beta.x for `FabricIQPreviewTool`, accepting the beta and re-solving the OpenAI pin.
  - Also confirm `gpt-5.6-luna` supports MCP/Fabric tools, or use gpt-5.4/5.5.
- **The trial can't host the data agent.** Data agent work (authoring, publish, evaluation) needs the paid F2 running. Keep `lh_portfolio`, `sm_portfolio` and the agent on capacities in the **same region** (Australia East). Enable Copilot plus **both cross-geo settings** (Australia to US) at least an hour ahead.
- **Split evaluation by identity.**
  - Foundry runs are **user-delegated only**, so they are attended runs as a signed-in user, not GitHub Actions.
  - The data-agent-only baseline can run unattended with the SP over MCP.
  - Pick one human identity for Foundry runs, and grant it Foundry User on project 3's project plus Read on the data agent and its sources (fabric@ is simplest), with a Fabric licence.
- **Use the lakehouse `gold` tables as the primary source for the frozen numeric set.** Lakehouse sources take example queries (up to 100, SQL validated, `gold.`-qualified), data source instructions and the SDK validator.
  - Add `sm_portfolio` only if a question needs a model measure, and put its guidance in **Prep for AI in the model's TMDL**: the DAX tool ignores agent instructions and models take no example queries.
  - Keep every answer at 25 rows or fewer.
- **RLS:** through `sm_portfolio`, leads are filtered and the owner sees all. The lakehouse source bypasses model RLS, so never give leads that source. SP queries against the RLS model will likely fail, so SP baselines use the lakehouse source only.
- **Lifecycle:** keep the DataAgent folder in git (commit from the dev workspace, data agent git still "preview" per its own page).
  - Add `find_replace` regexes for `workspaceId`/`artifactId` in `**/datasource.json` (draft and published).
  - After fabric-cicd, call **Publish Data Agent** REST as the SP, because unpublished agents aren't reachable by MCP or Foundry. Then point the Foundry connection at that environment's MCP URL.
- **Log what the tools return**: raw `FabricDataAgentToolCallOutput` / MCP content plus Foundry traces. Rely on the `evaluate_data_agent` `_steps` table or the Diagnostics export for generated SQL/DAX, since no doc promises query text in the tool output.
- **Budget**: F2 AUD 0.60/h. Each 15-question run is roughly tens of thousands of CU-seconds of background "AI query" that **bills in full when you pause**. Measure the first run in the Capacity Metrics app (LlmPlugin) before planning repeated passes.

---------------------------------------------------------------------
## Source list
- Data agent: /fabric/data-science/concept-data-agent, data-agent-tenant-settings, how-to-create-data-agent, data-agent-add-datasources, data-agent-configurations, data-agent-example-queries, data-agent-configuration-best-practices, semantic-model-best-practices, data-agent-source-control, data-agent-mcp-server, fabric-data-agent-sdk, evaluate-data-agent; /fabric/data-engineering/lakehouse-schemas (updated 2026-08-04; no data agent mention)
- Capacity/Copilot: /fabric/enterprise/fabric-features, fabric-operations, fabric-copilot-capacity, pause-resume; /fabric/fundamentals/copilot-fabric-overview, copilot-fabric-consumption; /fabric/admin/region-availability
- Git/REST: /fabric/cicd/git-integration/intro-to-git-integration; /rest/api/fabric/articles/item-management/definitions/data-agent-definition; /rest/api/fabric/dataagent/items (publish-data-agent, update-data-agent-definition)
- fabric-cicd: https://microsoft.github.io/fabric-cicd/latest/reference/item_types/, /latest/changelog/
- Foundry: /azure/foundry/agents/how-to/tools/fabric, tools/fabric-iq, configure-user-delegation; /azure/foundry/agents/concepts/limits-quotas-regions, tool-best-practice; https://azure.microsoft.com/en-au/pricing/details/foundry-agent-service
- Packages: https://api.nuget.org (azure.ai.projects versions), https://www.nuget.org/packages/Azure.AI.Projects/3.0.0-beta.5, https://pypi.org/pypi/fabric-data-agent-sdk/json, https://pypi.org/pypi/azure-ai-projects/json
- Prices: https://prices.azure.com/api/retail/prices (AUD, australiaeast, Microsoft Fabric)
- Local: E:\Projects\foundry-workshop-agent\README.md, Directory.Packages.props, src/Workshop.Agent/Engines/Foundry/*.cs, reflection over bin/Release/net10.0/Azure.AI.Projects.Agents.dll and Azure.AI.Extensions.OpenAI.dll
- Community: Fabric blog "Expanded data agent support for large data sources" (search snippet only; page returned 403)
