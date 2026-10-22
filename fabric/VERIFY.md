# Checks for plan 3 (live Fabric)

JSON files cannot hold comments, so the points in them that no Microsoft or fabric-cicd source
confirmed are listed here. Python files carry their own `# verify in plan 3` comments.

| File | Key | What to check |
|---|---|---|
| `lh_portfolio.Lakehouse/alm.settings.json` | `version`, `objectTypes` | Copied from fabric-cicd 1.4.0 `sample/workspace/Diabetes_LH.Lakehouse` with every state set to `Disabled`. Check that Fabric accepts `Disabled` on the `Shortcuts` parent and all eight sub-types, keeps `DataAccessRoles` disabled, and writes the same shape back on a commit. |
| `nb_*.Notebook/notebook-content.py` | `# META` `dependencies.lakehouse` | The git form for "default lakehouse in this workspace" is assumed to be the lakehouse's logicalId plus `default_lakehouse_workspace_id` = the empty GUID (the empty GUID is documented for environment references and OneLake shortcuts; fabric-cicd's lakehouse sample shows a real workspace GUID). Check that git sync binds the notebooks to `lh_portfolio` in the dev workspace, and compare with what Fabric writes on its first commit. |
| `nb_*.Notebook/notebook-content.py` | `%pip` cell | Whether `%pip` expands `{wheel}`, and whether the Python restart after an inline install loses `load_date` (Microsoft's library-management page says variables defined before the install are lost). |
| `pl_daily_load.DataPipeline/pipeline-content.json` | `typeProperties.parameters._inlineInstallationEnabled` | Written as `{"value": true, "type": "bool"}` (lower-case type as in Microsoft's fabric-toolbox pipelines; JSON boolean as the notebook-limitation page requires). Check that the activity enables `%pip` with this shape, not `"True"`/`"Bool"`. |
| `pl_daily_load.DataPipeline/pipeline-content.json` | `typeProperties` | Whether Fabric adds fields to a Notebook activity on commit (for example a connection under `externalReferences` for the run identity, or `sessionTag`) that a hand-written definition must carry. |
| `pl_daily_load.DataPipeline/pipeline-content.json` | `typeProperties.notebookId` / `workspaceId` | Notebook logicalIds with the empty workspace GUID. fabric-cicd documents same-workspace repointing; check that git sync into dev resolves them too. |
| `pl_daily_load.DataPipeline/pipeline-content.json` | `load_date` expression | `pipeline().TriggerTime` is UTC, so a 06:00 Brisbane (UTC+10) run gets the previous calendar day. Confirm that is the intended load date. |
| `pl_daily_load.DataPipeline/.schedules` | `configuration.localTimeZoneId` | `E. Australia Standard Time` is the Windows time zone ID for Brisbane (the REST API takes Windows IDs). Check the portal shows Brisbane, 06:00. |
| `pl_daily_load.DataPipeline/.schedules` | `configuration.startDateTime` | `2026-10-22T00:00:00` without `Z`, as in the git samples. The REST docs say a start time in the past "will trigger a job instantly"; check that enabling it in PROD does not start a run at deploy time. |
| `parameter.yml` | `find_replace[*].file_path` | Glob `**/notebook-content.py` combined with `item_type: Notebook`; check fabric-cicd's log shows the three replacements in both notebooks. |
