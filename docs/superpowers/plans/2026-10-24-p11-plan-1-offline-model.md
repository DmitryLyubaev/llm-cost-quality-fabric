# Project 11, plan 1: the semantic model, offline — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and test everything project 11 can build without Fabric, with no Azure, Fabric or
GitHub calls:
- gold's new keys and always-present tables;
- the access table loader;
- `OPTIMIZE` after loads;
- the Direct Lake semantic model as TMDL, with its security role and measures;
- the deploy changes;
- the model-versus-gold check.

**Architecture:** The model `fabric/sm_portfolio.SemanticModel/` is plain TMDL over `lh_portfolio`'s
`gold` and `security` schemas. Tests tie it to the gold tables a real load builds. The check
compares DAX results with the package's own answer functions, and only its `evaluate_dax` call is
live. The report is not in this plan: it is authored in dev during plan 2.

**Tech Stack:** project 9's stack (PySpark 4.1.1, delta-spark 4.2.0, Python 3.13, fabric-cicd 1.4.0);
TMDL (compatibility level 1604 or higher); DAX; semantic link (`sempy`, only inside the notebook).

**Spec:** `docs/superpowers/specs/2026-10-24-project-11-semantic-model-design.md` (approved
24 Oct 2026). **Facts:** `docs/research/2026-10-24-semantic-model-facts.md`.

**Plan 2** (written after this merges) is the live runbook (spec §4.7).

## Global Constraints

- Project 9's rules hold:
  - commits as `Dmitry Lyubaev <Dmitry.Lyubaev@gmail.com>`;
  - no push without the owner's yes;
  - no Azure, Fabric or GitHub calls in this plan;
  - Spark 4.1.1 / delta-spark 4.2.0 / Python 3.13, session time zone UTC, tests in WSL.
- No identifiers committed: no UPN, tenant or subscription ID, Entra object ID, or URL, apart
  from the OneLake expression's host. Fabric workspace and item GUIDs are allowed (owner, 21 Oct).
  The access table's UPNs live only in a git-ignored `private/access.csv` and in OneLake.
- Storage mode: Direct Lake on OneLake. Every partition is `mode: directLake` with
  `schemaName: gold` (or `security`) and the bare table name. No calculated columns and no
  calculated tables.
- Security: one role, `ProjectLead`. The owner is not a member: workspace Admins aren't filtered.
- Every measure's description names the gold answer it mirrors. The model has no blended "score"
  and no share measure (spec 4.1).
- Costs stay in the billing currency. Estimated and actual cost are never summed.
- The check tolerances: counts exact; money and rates within `0.005`.

## Review Focus

1. **A gold column renamed or retyped by a later change to project 9** makes the model drift in
   silence. A test compares every model column with the schema a real load writes. *(Task 4.)*
2. **An access file with mixed-case UPNs, two projects for one user, a blank UPN, an unknown project
   or a duplicate row.** Rows are stored lower-case, the bad ones are rejected, and the error names
   the line number, never the UPN. *(Task 2.)*
3. **A filled session table, when the "always present" step runs.** It is never overwritten or
   emptied. *(Task 2.)*
4. **Gold zero against DAX blank, or both null:** these count as equal in the check. A real
   difference fails with the measure name, the key and both values. *(Task 6.)*
5. **The P11 connection secret unset** (every prod deploy until plan 2): the model and report are
   skipped, `refresh-model` prints a skip, and projects 9 and 10 deploy as before. *(Task 5.)*

---

## File structure (new or changed)

```
src/lakehouse/seeds/study_projects.csv        study → project, with evidence (Task 1)
src/lakehouse/gold/build.py                   keys + project columns; always-present tables (Tasks 1–2)
src/lakehouse/paths.py                        layer "security" (Task 2)
src/lakehouse/security.py                     access loader (Task 2)
src/lakehouse/run.py, gateway/build.py        OPTIMIZE after writes (Task 3)
fabric/sm_portfolio.SemanticModel/            the model, TMDL (Task 4)
fabric/nb_access_load.Notebook/, fabric/nb_model_check.Notebook/   (Tasks 2, 6)
fabric/parameter.yml, .deploy/deploy.py, .github/workflows/deploy.yml   (Task 5)
src/lakehouse/semantic.py                     DAX queries + comparison + check (Task 6)
src/lakehouse/fabric_entry.py                 load_access, check_model (Tasks 2, 6)
tools/check_hygiene.py                        reject *.onmicrosoft.com addresses (Task 4)
fabric/VERIFY.md                              "Project 11, plan 2" section (Tasks 4–6)
```

---

### Task 1: Gold keys and project columns

**Files:**
- Create `src/lakehouse/seeds/study_projects.csv`.
- Modify `src/lakehouse/gold/build.py` and `src/lakehouse/answers.py`, which drops `STUDY_PROJECT`
  and reads the seed.
- Test: `tests/test_gold.py` and `tests/test_seeding.py`.

**Interfaces:**
- Produces:
  - `study_projects.csv` with columns `study,project,evidence`, holding
    `p1-three-arm→p1`, `p2-retrieval→p2` and `p3-agent→p3`, each citing its project's report;
  - `read_study_projects(seeds_dir) -> dict[str, str]` in `lakehouse.seeding`.
- Gold columns:
  - `dim_study`: `study_key` (= `study|run_id`), `project`;
  - `dim_arm`: `arm_key` (= `study|arm`), `project`;
  - `fact_eval_attempt`: `study_key`, `arm_key`, `project`. `deployment_sk` is already there.
  - Key text is `concat_ws("|", ...)`.

- [ ] **Step 1: Failing tests** (use `loaded_lake`):
  - `test_study_and_arm_keys_are_unique`: `dim_study.study_key` and `dim_arm.arm_key` are unique,
    and so is `dim_model_deployment.deployment_sk`.
  - `test_every_fact_key_resolves`: every `fact_eval_attempt` row's `study_key`, `arm_key` and
    `deployment_sk` exists in its dimension.
  - `test_eval_rows_carry_their_project`: the project matches the seed map, and is never null.
  - `test_a_study_missing_from_the_seed_fails_the_build`: a ValueError that names the study.
  - `cost_per_result` gives the same rows as before the change (pin the current fixture values).
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS
  (`tests/test_gold.py tests/test_seeding.py tests/test_checks.py`).
- [ ] **Step 5: Commit** `Add study and arm keys and project columns to gold`

### Task 2: Always-present tables, the security layer and the access loader

**Files:**
- Create `src/lakehouse/security.py`, `fabric/nb_access_load.Notebook/` and `tests/test_security.py`.
- Modify `src/lakehouse/paths.py` (`LAYERS` gains `"security"`), `src/lakehouse/gold/build.py`,
  `src/lakehouse/fabric_entry.py`, `.gitignore` (`private/access.csv` is already under `private/`:
  check it) and README's grain table (one row for `security.user_project`).

**Interfaces:**
- `ensure_table(spark, path: str, schema: StructType) -> bool`: creates an empty Delta table when
  `path` holds none, and returns whether it created one. It never touches an existing table.
  - `build_gold` calls it for `gold.fact_gateway_call`, with the schema `lakehouse.gateway.build`
    writes, and for `security.user_project`.
- `USER_PROJECT_SCHEMA = upn string not null, project string not null`.
- `load_access(spark, paths, csv_path: str, projects: list[str]) -> dict`:
  - reads `upn,project`;
  - strips and lower-cases the UPN;
  - rejects a blank UPN, an unknown project or a duplicate pair, with a ValueError naming the line
    number only;
  - overwrites `security.user_project`;
  - returns `{"rows": n, "users": n, "projects": n}`, and never prints a UPN.
- `fabric_entry.load_access(context: dict, csv_dir: str = "Files/private/access") -> dict` reads the
  single CSV in that folder.
- `nb_access_load`: `%pip` first, then one call, printing the JSON. It has no parameters.

- [ ] **Step 1: Failing tests:**
  - `test_ensure_table_creates_an_empty_table_once`;
  - `test_ensure_table_never_overwrites_a_filled_table` *(Review Focus 3)*;
  - `test_build_gold_leaves_an_empty_gateway_table_when_no_session_ran`;
  - access loader tests: mixed case gives lower case; two projects for one user give two rows;
    a blank UPN, an unknown project and a duplicate each raise with `line N` and no UPN in the
    message *(Review Focus 2)*; the return dict holds counts only;
  - item test for `nb_access_load`, in the style of `nb_load`'s.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS (`tests/test_security.py
  tests/test_gold.py tests/test_paths.py tests/test_fabric_items.py tests/test_fabric_entry.py`).
- [ ] **Step 5: Commit** `Keep session and security tables present; load the access table`

### Task 3: OPTIMIZE after each load

**Files:** Modify `src/lakehouse/run.py` (after gold) and `src/lakehouse/gateway/build.py` (after
the silver MERGE and gold). Test: `tests/test_run.py` and `tests/test_gateway_lake.py`.

**Interfaces:** `optimize_tables(spark, paths: list[str]) -> None` in `lakehouse.gold.build` runs
`DeltaTable.forPath(spark, p).optimize().executeCompaction()` for each path. `run_load` calls it for
every `GOLD_TABLES` path, and `load_gateway` for its silver and gold tables. The reason (Direct Lake
on OneLake errors past 1,000 Parquet files per table) goes in a comment.

- [ ] **Step 1: Failing tests:**
  - after two `run_load`s, each gold table's latest Delta history entry is an `OPTIMIZE`;
  - the re-run fingerprint is still unchanged (project 9's check);
  - the gateway load runs OPTIMIZE on both of its tables.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS. **Step 5: Commit**
  `Compact gold and gateway tables after each load`

### Task 4: The semantic model `sm_portfolio`

**Files:**
- Create `fabric/sm_portfolio.SemanticModel/`, holding:
  - `.platform` (type `SemanticModel`, a new logicalId);
  - `definition.pbism`;
  - `definition/database.tmdl` and `model.tmdl` (culture `en-AU`);
  - `expressions.tmdl`;
  - `relationships.tmdl`;
  - `tables/*.tmdl`;
  - `roles/ProjectLead.tmdl`.
- Create `tests/test_semantic_model.py`.
- Modify `tools/check_hygiene.py` and `fabric/VERIFY.md`.

**Content:**
- **`expressions.tmdl`** has one expression, `DirectLake - lh_portfolio`, with the research file's
  shape:
  `AzureStorage.DataLake("https://onelake.dfs.fabric.microsoft.com/<ws>/<lh>", [HierarchicalNavigation=true])`.
  Both GUIDs are the empty GUID until plan 2 commits dev's real IDs. That gets a VERIFY row:
  dev's copy can't frame until then.
- **Tables** are every name in `GOLD_TABLES` and `SESSION_TABLES`, plus `user_project`. Each table:
  - has a partition `mode: directLake`, `entityName` the bare name, `schemaName` `gold` or
    `security`, and `expressionSource` the expression above;
  - has columns with `sourceColumn` equal to the gold column name;
  - maps Spark types to TMDL types: string→`string`, int and long→`int64`, double→`double`,
    boolean→`boolean`, date and timestamp→`dateTime`, decimal→`decimal`;
  - hides `user_project` and every `*_key` / `*_sk` column.
- **Relationships**, all single-direction and many-to-one:
  - each fact's date column → `dim_date[date]`;
  - each `project` → `dim_project[project]`;
  - `fact_eval_attempt[study_key]` → `dim_study[study_key]`;
  - `fact_eval_attempt[arm_key]` → `dim_arm[arm_key]`;
  - `fact_eval_attempt[deployment_sk]` → `dim_model_deployment[deployment_sk]`.

  `user_project` has no relationship.
- **Role `ProjectLead`**, with `modelPermission: read`:
  - `user_project` is filtered to `[upn] = USERPRINCIPALNAME()`;
  - every other table with a `project` column is filtered to
    `[project] IN CALCULATETABLE(VALUES(user_project[project]), user_project[upn] = USERPRINCIPALNAME())`.

  DAX compares strings case-insensitively, and the loader stores lower case.
- **Measures**, on a hidden `_Measures` table or the facts, in the folders `Spend`, `Quality` and
  `Gateway`, with the exact names from spec 4.1. Each description is
  `Mirrors lakehouse.<module>.<function>`.

- [ ] **Step 1: Failing tests:**
  - **`test_model_tables_match_gold`**: the set of table names equals `GOLD_TABLES` +
    `SESSION_TABLES` + `{"user_project"}`.
  - **`test_model_columns_match_a_real_load`**: uses `loaded_lake` plus the empty session tables.
    Every gold column is in the model with the mapped type, and the model has no column gold
    lacks *(Review Focus 1)*.
  - **`test_every_partition_is_direct_lake_on_onelake`**: `mode`, `schemaName` and
    `expressionSource` are as above.
  - **`test_one_sides_are_unique`**, on `loaded_lake`: every relationship's to-column is unique.
  - **`test_role_filters_every_table_with_a_project_column`**, against the exact filter text.
  - **`test_every_measure_names_its_gold_answer`**: each `Mirrors lakehouse.x.y` resolves to a real
    function.
  - **`test_no_identifiers_in_the_model`**: no `@`, no non-empty GUID except logicalIds, and no
    URL other than the OneLake host.
  - **Hygiene:** `check_hygiene` rejects any `@<name>.onmicrosoft.com` address (generic, so it
    names no tenant), and a test pins it.
- [ ] **Step 2:** FAIL. **Step 3:** Write the TMDL, following the research file's shape. Anything
  Learn didn't confirm gets a row in a new "Project 11, plan 2" section of `fabric/VERIFY.md`.
- [ ] **Step 4:** PASS (`tests/test_semantic_model.py tests/test_hygiene.py tests/test_fabric_items.py`).
- [ ] **Step 5: Commit** `Add the Direct Lake semantic model with project-level security`

### Task 5: Deploy — scope, rebinding, connection and refresh

**Files:**
- Modify `fabric/parameter.yml`, `.deploy/deploy.py` and `.github/workflows/deploy.yml`.
- Tests: `tests/test_deploy.py`, `tests/test_workflows.py` and `tests/test_fabric_items.py`.

**Interfaces:**
- **Gate:** `P11_MODEL_CONNECTION_ID`, the second gate, built like R13 (project 10).
  - Unset or empty: `SemanticModel` and `Report` are out of scope, publish prints
    `publish: model and report skipped (no P11_MODEL_CONNECTION_ID)`, and the in-process empty-GUID
    stand-in satisfies fabric-cicd's `$ENV` validation.
  - Set: both are in scope.
  - The two gates are independent.
- **`parameter.yml`:**
  - two regex `find_replace` rules on `**/definition/expressions.tmdl` (item type `SemanticModel`):
    the workspace capture group → `$workspace.$id`, and the lakehouse capture group →
    `$items.Lakehouse.lh_portfolio.$id`, both PROD only;
  - plus the connection binding for `sm_portfolio`:
    - fabric-cicd's `semantic_model_binding` with `$ENV:P11_MODEL_CONNECTION_ID`, if 1.4.0's source
      shows that section resolves `$ENV`;
    - otherwise `deploy.py` binds through
      `POST /v1/workspaces/{ws}/semanticModels/{id}/bindConnection` inside `refresh-model`, before
      the refresh.

    Record which, with the source file read.
- **`refresh-model`** (env `FABRIC_WORKSPACE_ID`, gated):
  - looks up `sm_portfolio` by name, paged like the other lookups;
  - binds, if fabric-cicd didn't;
  - starts a refresh with the refresh API Learn documents for semantic models, and polls the
    status to completion (cap: 10 minutes);
  - prints `refresh-model: ok`.

  Same error rules as the other steps: status before body, and only the type on unexpected errors.
- **`deploy.yml` prod job:**
  - `P11_MODEL_CONNECTION_ID` goes on the publish step;
  - a new step, `Refresh the semantic model`, runs `refresh-model` after `ensure-gateway-shortcut`;
  - all secrets stay step-scoped.

- [ ] **Step 1: Failing tests:**
  - **Every `find_replace` rule evaluated** on the model file, as in project 10: each regex matches
    exactly once, and PROD replaces only the two GUIDs.
  - **Gate off/on** for publish and `refresh-model` *(Review Focus 5)*.
  - **The refresh request:** URL and body.
  - **Polling:** success, failure (exit 1 with the status) and timeout.
  - **The binding path chosen.**
  - **The workflow's step order and secrets.**
  - **No IDs in output.**
- [ ] **Step 2:** FAIL. **Step 3:** Implement. Check the refresh and bindConnection APIs and their
  token scope on Learn, and cite them in comments. Anything unconfirmed gets a VERIFY row.
- [ ] **Step 4:** PASS (`tests/test_deploy.py tests/test_workflows.py tests/test_fabric_items.py tests/test_hygiene.py`).
- [ ] **Step 5: Commit** `Deploy and refresh the semantic model behind its own gate`

### Task 6: The model-versus-gold check

**Files:**
- Create `src/lakehouse/semantic.py`, `fabric/nb_model_check.Notebook/` and `tests/test_semantic_check.py`.
- Modify `src/lakehouse/fabric_entry.py` and `fabric/VERIFY.md`.

**Interfaces:**
- `QUERIES: dict[str, str]`: one DAX `EVALUATE SUMMARIZECOLUMNS(...)` per gold answer, at the
  answer's own grain.
  - `cost_per_project`: `project, billing_currency, total`;
  - `quality_across_models`: study, arm, model, model version, `n` and each metric;
  - `cost_per_result`: per project;
  - `cost_per_gateway_call`: per session, call date and currency.

  The column aliases equal the answer's column names.
- `compare(answer: str, expected: list[dict], actual: list[dict], keys: list[str]) -> list[Mismatch]`
  is pure Python.
  - Counts must be exact; floats must be within `0.005`.
  - Gold `0` against DAX blank or missing counts as equal, and so does null against null
    *(Review Focus 4)*.
  - `Mismatch(answer, key: tuple, column, expected, actual)`.
- `check_model_matches_gold(spark, paths, evaluate: Callable[[str, str | None], list[dict]], sessions: list[str]) -> CheckResult`:
  - `evaluate(dax, user)` runs as the owner when `user` is None;
  - as the owner, every query must equal its answer;
  - then, for each UPN in `security.user_project`, `cost_per_project` and `quality_across_models`
    run as that user. Only that user's projects may appear, and their rows must equal the owner's.
  - The result detail names projects, never UPNs.
- `fabric_entry.check_model(context: dict, sessions: str = "") -> dict` wraps
  `sempy.fabric.evaluate_dax(dataset="sm_portfolio", dax_string=..., effective_user_name=...)`. Check
  the parameter names in sempy 0.14.x. It raises `QualityCheckFailed` on any mismatch.
- `nb_model_check`: `%pip` first; a parameters cell `sessions = ""`; one call. A markdown cell says
  to run it as a user, never from the pipeline, because the service principal is unsupported.

- [ ] **Step 1: Failing tests:**
  - `compare` cases: equal; within tolerance; over tolerance; zero against blank; null against null;
    an extra row; a missing row.
  - `check_model_matches_gold` with a fake `evaluate` that serves gold's own answers: it passes.
    Then with one tampered figure it fails, naming the measure and key. Then a fake user who sees a
    second project: it fails without the UPN in the detail.
  - Every query alias set equals its answer function's columns, checked on `loaded_lake`.
  - Item test for `nb_model_check`.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS (`tests/test_semantic_check.py
  tests/test_fabric_items.py tests/test_fabric_entry.py`).
- [ ] **Step 5: Commit** `Check the semantic model against gold, as the owner and as each lead`

---

## After plan 1

- Full suite and hygiene; a PR in this repo, merged on the owner's yes, with the deploy run
  cancelled.
- A tracker line, then plan 2, the live runbook from spec §4.7. Its first step commits dev's real
  workspace and lakehouse IDs into `expressions.tmdl`.
