# Checks for the capstone's live build

JSON files cannot hold comments, so the points in `fabric-capstone/` that no Microsoft or
fabric-cicd source confirmed are listed here, for capstone plan 2 (the live run). The format facts
come from `docs/research/2026-10-25-data-agent-facts.md`, sections 2 and 3. This file sits outside
`da_portfolio.DataAgent/`, so the freeze's `data_agent` hash does not cover it.

## The data agent `da_portfolio.DataAgent/`

| File | Key | What to check |
|---|---|---|
| (folder) | `Files/Config/...` casing | **Confirmed live (16 Nov 2026).** The first `publish-capstone` was accepted, and Fabric's own export (Get Item Definition) uses `Files/Config/...`, as the folder does. |
| (folder) | no `published/` | **Confirmed live (16 Nov 2026).** fabric-cicd deployed the definition without a `published/` folder; after the Publish Data Agent call, Fabric's export holds `published/` files identical to `draft/` and a `publish_info.json`. Only `draft/` is committed ("Don't directly make changes to the published folder"). |
| `Files/Config/data_agent.json` | `$schema` | Fabric's export writes the full URL `https://developer.microsoft.com/json-schemas/fabric/item/dataAgent/definition/dataAgent/2.1.0/schema.json`; the bare `"2.1.0"` was accepted on deploy, but the file now holds Fabric's form (16 Nov 2026) so the frozen bytes match what Fabric writes. |
| `Files/Config/draft/stage_config.json` | `$schema`, `aiInstructions` | `$schema` is Fabric's URL (`.../definition/stageConfiguration/1.0.0/schema.json`), as its export writes it (16 Nov 2026). The agent-level instructions (4,225 characters; the limit is 15,000) round-trip unchanged. |
| `draft/lakehouse-tables-lh_capstone/datasource.json` | `workspaceId`, `artifactId` | Both the empty GUID in git. `publish-capstone` (Task 5) must rewrite both with `find_replace` regexes to the capstone workspace and `lh_capstone`'s deployed ID. Check the deployed agent shows `lh_capstone` as its source. Whether Fabric rewrites these to logical IDs on a git commit is not documented. |
| `draft/lakehouse-tables-lh_capstone/datasource.json` | `$schema`, `elements` | **Resolved live (16 Nov 2026)** from Fabric's export: `$schema` is `.../definition/dataSource/1.0.0/schema.json`; the keys run `$schema`, `artifactId`, `workspaceId`, `dataSourceInstructions`, `displayName`, `type`, `userDescription`, `metadata` (null), `elements`; one `lakehouse_tables.schema` element `gold` with the nine `lakehouse_tables.table` children, each `is_selected: true` with empty `children`, and every element's keys run `id`, `is_selected`, `display_name`, `type`, `description`, `children`, with `id` and `description` null. So Fabric carries an `id` key on each element but needs no value in it (Microsoft's definition table marks it required). The folder now holds exactly that form, with the git placeholders for the two IDs. |
| `draft/lakehouse-tables-lh_capstone/datasource.json` | `dataSourceInstructions`, `userDescription` | Check both show in the source's settings after deploy. |
| `draft/lakehouse-tables-lh_capstone/fewshots.json` | `$schema`, `fewShots` | **Confirmed live (16 Nov 2026):** Fabric's export uses the top-level key `fewShots` with the 20 `{id, question, query}` examples unchanged; `$schema` is now Fabric's URL (`.../definition/fewShots/1.0.0/schema.json`). |
| `draft/lakehouse-tables-lh_capstone/fewshots.json` | each `query` | Tested offline in Spark SQL (`tests/test_capstone_agent.py`), kept to what Spark SQL and T-SQL share (no `LIMIT`/`TOP`, no backticks or double quotes, bit columns only inside `CAST`, `CAST(... AS FLOAT)` for rates). **Checked live (16 Nov 2026):** the SDK's `evaluate_few_shots` (fabric-data-agent-sdk 0.1.36a0) on the deployed agent's draft passed 20 of 20 examples with 0 conflicts. |
| `draft/lakehouse-tables-lh_capstone/fewshots.json` | gateway example | It names the session `gateway-demo`, which the fixture test adds; prod has `p10-2026-11-15` (q12's session, set on the run branch at step 8). The example teaches the shape, so it deliberately does not name q12's session. |

## The lakehouse `lh_capstone.Lakehouse/`

| File | Key | What to check |
|---|---|---|
| `lakehouse.metadata.json` | `defaultSchema` | `{"defaultSchema": "dbo"}` marks a schema-enabled lakehouse in its git form, as for `lh_portfolio`. Check the deployed lakehouse has schemas enabled, so the snapshot's `Tables/gold/<table>` paths show as the `gold` schema in the SQL endpoint and the data agent's Explorer. |
| `alm.settings.json` | whole file | A byte copy of `lh_portfolio`'s (all shortcut types and data access roles disabled); see `fabric/VERIFY.md`. |

## The notebooks

| File | Key | What to check |
|---|---|---|
| `nb_capstone_*.Notebook/notebook-content.py` | `# META` `dependencies.lakehouse` | Git holds `lh_capstone`'s logicalId with the empty workspace GUID. The runtime can't use that form: project 9's first dev run failed on it (`LakehouseWorkspaceId is not a valid GUID`), so `fabric/` now commits dev's real IDs. The capstone workspace exists only for the live run, so `parameter.yml`'s three `Notebook` rules swap in the capstone workspace and `lh_capstone`'s deployed ID on every `publish-capstone`, as project 9's PROD rules do. Check after the first publish (Get Item Definition on each notebook) that `default_lakehouse`, `known_lakehouses` and `default_lakehouse_workspace_id` hold those real IDs, before either notebook runs. Never connect the capstone workspace to git: "Update from Git" would bring the placeholder form back. |
| `nb_capstone_*.Notebook/notebook-content.py` | `%pip` cell | The wheel is read from `lh_capstone`'s `Files/wheels/` (the notebooks' default lakehouse), so upload it there before the first run. As in `fabric/`, check the parameters still reach the call cell after the `%pip` restart. |
| `nb_capstone_snapshot.Notebook/notebook-content.py` | parameters | `source_workspace_id` and `source_lakehouse_id` (prod and prod `lh_portfolio`) are typed in when the notebook is run and never committed; every run copies all nine tables. Check the run identity can read prod `lh_portfolio` across workspaces and capacities (trial to F2) through its OneLake `abfss://` path, and that the copy lands as `lh_capstone`'s `gold` schema. **Checked 16 Nov 2026:** run through the job API as `fabric@` with both IDs passed as run parameters (never saved), plus `_inlineInstallationEnabled` (bool `true`) for the `%pip` cell. It read prod `lh_portfolio` from the F2 workspace, completed in about two minutes, and all nine tables landed under `lh_capstone`'s `Tables/gold/`. |
| `nb_capstone_snapshot.Notebook/notebook-content.py` | `Files/capstone/snapshot.json` | Written through the default lakehouse's mount (`/lakehouse/default/Files`). Check it appears under `lh_capstone`'s Files and record its `snapshot_time` for the freeze. The `versions` are the source tables' Delta versions, read with `versionAsOf`; check OneLake honours time travel across workspaces (a vacuumed version would fail the read, not change it). **Checked 16 Nov 2026:** `snapshot.json` appeared under `lh_capstone`'s `Files/capstone/` with `snapshot_time` 2026-11-16T05:44:19Z, now `capstone/snapshot.json`. Each recorded version was the source's latest, so this run did not test time travel to an older version. Every table's row count matched three ways: prod at the recorded version, `lh_capstone`, and `snapshot.json` (counted outside Fabric with `deltalake`, which handles the tables' deletion vectors). |
| `nb_capstone_expected.Notebook/notebook-content.py` | `Files/capstone/` | Reads the uploaded `questions.json` and `fewshots-sources.json`, and writes `expected.json` beside them through the mount. Download `expected.json` byte for byte into `capstone/` (the freeze hashes its bytes). |
| `nb_capstone_expected.Notebook/notebook-content.py` | `Files/capstone/leaks.json` | The value-based leak check (`lakehouse.capstone.leaks.value_leaks`) on the snapshot: each example whose result shows a single-cell frozen answer, as `{example_id, question_id}`. Check it holds `[]` before the freeze; if not, fix the example and re-run (runbook step 8). |

## Grain lines and projects

| File | Key | What to check |
|---|---|---|
| `stage_config.json`, `datasource.json` | table descriptions | Each carries `lakehouse.gold.build.GRAINS[table]` word for word (a test enforces it). Task 6 added a `capstone` project, which changed `dim_project`'s grain line; both descriptions already carry the new line. The instructions name no fixed list of projects: they point to `dim_project`. |

## The deploy `publish-capstone` and `parameter.yml`

| File | Key | What to check |
|---|---|---|
| `.deploy/deploy.py` | `CAPSTONE_WORKSPACE_ID` | The capstone workspace comes from its own variable, never `FABRIC_WORKSPACE_ID`, so a shell with prod's ID exported cannot deploy the capstone into prod; unset, the step exits 2 before any call. Export the capstone workspace's ID (from `infra/capstone`'s output) in the `fabric@` shell before the run, and check it is not prod's. |
| `parameter.yml` | the five `find_replace` rules | fabric-cicd reads this file because it is in the repository directory it is given (`fabric-capstone/`). Check the deploy log shows the parameter file validated for `CAPSTONE`, the deployed `datasource.json` holds the capstone workspace and `lh_capstone`'s ID, and both notebooks' default lakehouse holds them too (the notebooks row). |
| `.deploy/deploy.py` | Publish Data Agent response | Learn documents `200 OK` only, so any other status, `202` included, fails the step. If the live call answers `202` with a `Location`, the publish is long-running after all: add a poll before relying on it. Check the agent shows as published afterwards. |
| `.deploy/deploy.py` | `DATA_AGENT_DESCRIPTION` | The `publishedDescription` sent on every run. Check it shows as the agent's published description. Fabric stores it in `Files/Config/publish_info.json`, which is not committed, so the freeze does not hash it. |
