# Checks for the capstone's live build

JSON files cannot hold comments, so the points in `fabric-capstone/` that no Microsoft or
fabric-cicd source confirmed are listed here, for capstone plan 2 (the live run). The format facts
come from `docs/research/2026-10-25-data-agent-facts.md`, sections 2 and 3. This file sits outside
`da_portfolio.DataAgent/`, so the freeze's `data_agent` hash does not cover it.

## The data agent `da_portfolio.DataAgent/`

| File | Key | What to check |
|---|---|---|
| (folder) | `Files/Config/...` casing | The REST definition page uses `Files/Config/...`; the source-control page shows `files/config/...` for the git form. The folder follows the REST casing, which fabric-cicd uploads as the definition. Check the first deploy is accepted, and compare with what Fabric writes on its first git commit from a workspace (if it writes `files/config`, rename and re-freeze before the freeze, never after). |
| (folder) | no `published/` | Only `draft/` is committed ("Don't directly make changes to the published folder"). The Publish Data Agent call after deploy creates the published stage. Check fabric-cicd deploys a definition with no `published/` folder, and that the published stage then mirrors the draft. |
| `Files/Config/data_agent.json` | `$schema` | `"2.1.0"`, as the research file records from the definition page. Check the deploy accepts it. |
| `Files/Config/draft/stage_config.json` | `$schema`, `aiInstructions` | `"1.0.0"` and the agent-level instructions (4,225 characters; the limit is 15,000). Check the instructions show under the agent's AI instructions after deploy. |
| `draft/lakehouse-tables-lh_capstone/datasource.json` | `workspaceId`, `artifactId` | Both the empty GUID in git. `publish-capstone` (Task 5) must rewrite both with `find_replace` regexes to the capstone workspace and `lh_capstone`'s deployed ID. Check the deployed agent shows `lh_capstone` as its source. Whether Fabric rewrites these to logical IDs on a git commit is not documented. |
| `draft/lakehouse-tables-lh_capstone/datasource.json` | `$schema`, `elements` | `"1.0.0"`; one `lakehouse_tables.schema` element `gold` with the nine `lakehouse_tables.table` children, each `is_selected: true`, with empty `children` (column selection is ignored) and no element `id`s. The element field names (`display_name`, `type`, `is_selected`, `children`) and the schema level for a schema-enabled lakehouse are inferred (research file, section 2: schema-enabled lakehouses are neither confirmed nor ruled out). Check the Explorer shows exactly the nine `gold` tables selected; if Fabric needs element IDs or another shape, take its own export as the template and re-freeze. |
| `draft/lakehouse-tables-lh_capstone/datasource.json` | `dataSourceInstructions`, `userDescription` | Check both show in the source's settings after deploy. |
| `draft/lakehouse-tables-lh_capstone/fewshots.json` | `$schema`, `fewShots` | `"1.0.0"` and a `fewShots` array of `{id, question, query}` with GUID ids. The top-level key name is inferred. Check all 20 examples show under the source's example queries. |
| `draft/lakehouse-tables-lh_capstone/fewshots.json` | each `query` | Tested offline in Spark SQL only (`tests/test_capstone_agent.py`), kept to what Spark SQL and T-SQL share (no `LIMIT`/`TOP`, no backticks or double quotes, bit columns only inside `CAST`, `CAST(... AS FLOAT)` for rates). The data agent runs them as T-SQL on the SQL analytics endpoint, and invalid examples are silently dropped. Run the SDK's `evaluate_few_shots` on the deployed agent and fix any example it rejects before the freeze. Points to watch: `session` as a column name, `CAST(bit AS FLOAT)` averages, `CAST('yyyy-MM-dd' AS DATE)`, CTE names that equal column names (`calls`, `results`). |
| `draft/lakehouse-tables-lh_capstone/fewshots.json` | gateway example | It names the session `gateway-demo`, which the fixture test adds; prod has `p10-session` (q12's session). The example teaches the shape, so it deliberately does not name q12's session. |

## The lakehouse `lh_capstone.Lakehouse/`

| File | Key | What to check |
|---|---|---|
| `lakehouse.metadata.json` | `defaultSchema` | `{"defaultSchema": "dbo"}` marks a schema-enabled lakehouse in its git form, as for `lh_portfolio`. Check the deployed lakehouse has schemas enabled, so the snapshot's `Tables/gold/<table>` paths show as the `gold` schema in the SQL endpoint and the data agent's Explorer. |
| `alm.settings.json` | whole file | A byte copy of `lh_portfolio`'s (all shortcut types and data access roles disabled); see `fabric/VERIFY.md`. |

## The notebooks

| File | Key | What to check |
|---|---|---|
| `nb_capstone_*.Notebook/notebook-content.py` | `# META` `dependencies.lakehouse` | `lh_capstone`'s logicalId with the empty workspace GUID, the form `fabric/` uses (see `fabric/VERIFY.md`). Check the deploy binds both notebooks to `lh_capstone` in the capstone workspace. |
| `nb_capstone_*.Notebook/notebook-content.py` | `%pip` cell | The wheel is read from `lh_capstone`'s `Files/wheels/` (the notebooks' default lakehouse), so upload it there before the first run. As in `fabric/`, check the parameters still reach the call cell after the `%pip` restart. |
| `nb_capstone_snapshot.Notebook/notebook-content.py` | parameters | `source_workspace_id` and `source_lakehouse_id` (prod and prod `lh_portfolio`) are typed in when the notebook is run and never committed; every run copies all nine tables. Check the run identity can read prod `lh_portfolio` across workspaces and capacities (trial to F2) through its OneLake `abfss://` path, and that the copy lands as `lh_capstone`'s `gold` schema. |
| `nb_capstone_snapshot.Notebook/notebook-content.py` | `Files/capstone/snapshot.json` | Written through the default lakehouse's mount (`/lakehouse/default/Files`). Check it appears under `lh_capstone`'s Files and record its `snapshot_time` for the freeze. The `versions` are the source tables' Delta versions, read with `versionAsOf`; check OneLake honours time travel across workspaces (a vacuumed version would fail the read, not change it). |
| `nb_capstone_expected.Notebook/notebook-content.py` | `Files/capstone/` | Reads the uploaded `questions.json` and writes `expected.json` beside it through the mount. Download `expected.json` byte for byte into `capstone/` (the freeze hashes its bytes). |

## Grain lines and projects

| File | Key | What to check |
|---|---|---|
| `stage_config.json`, `datasource.json` | table descriptions | Each carries `lakehouse.gold.build.GRAINS[table]` word for word (a test enforces it). Task 6 added a `capstone` project, which changed `dim_project`'s grain line; both descriptions already carry the new line. The instructions name no fixed list of projects: they point to `dim_project`. |

## The deploy `publish-capstone` and `parameter.yml`

| File | Key | What to check |
|---|---|---|
| `.deploy/deploy.py` | `CAPSTONE_WORKSPACE_ID` | The capstone workspace comes from its own variable, never `FABRIC_WORKSPACE_ID`, so a shell with prod's ID exported cannot deploy the capstone into prod; unset, the step exits 2 before any call. Export the capstone workspace's ID (from `infra/capstone`'s output) in the `fabric@` shell before the run, and check it is not prod's. |
| `parameter.yml` | the two `find_replace` rules | fabric-cicd reads this file because it is in the repository directory it is given (`fabric-capstone/`). Check the deploy log shows the parameter file validated for `CAPSTONE`, and the deployed `datasource.json` holds the capstone workspace and `lh_capstone`'s ID. |
| `.deploy/deploy.py` | Publish Data Agent response | Learn documents `200 OK` only, so any other status, `202` included, fails the step. If the live call answers `202` with a `Location`, the publish is long-running after all: add a poll before relying on it. Check the agent shows as published afterwards. |
| `.deploy/deploy.py` | `DATA_AGENT_DESCRIPTION` | The `publishedDescription` sent on every run. Check it shows as the agent's published description. Fabric stores it in `Files/Config/publish_info.json`, which is not committed, so the freeze does not hash it. |
