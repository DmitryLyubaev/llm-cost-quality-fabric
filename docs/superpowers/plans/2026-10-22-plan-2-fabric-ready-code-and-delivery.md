# Project 9, plan 2: Fabric-ready code, items and delivery (offline) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make plan 1's package run unchanged inside a Fabric notebook, and write, as tested files in
the repo, everything plan 3's live session deploys: the Fabric items (lakehouse, notebooks,
pipeline), the wheel upload and `fabric-cicd` deploy, the GitHub Actions workflows, and the Terraform
for the workspaces and the cost-export storage. Nothing here touches Azure, Fabric or GitHub.

**Architecture:** One schema-enabled lakehouse per workspace. Medallion layers are schemas
(`Tables/silver/…`, `Tables/gold/…`, `Tables/reject/…`); bronze is `Files/bronze/…`. The package
ships as a wheel that the deploy uploads to `Files/wheels/`; each notebook `%pip install`s it from the
lakehouse mount and calls one entry point. Dev items arrive by Fabric git sync from the `dev` branch;
prod items arrive only by `fabric-cicd` from `main`, as a service principal, behind an approval.

**Tech Stack:** plan 1's stack, plus `fabric-cicd==1.4.0`, `azure-identity`,
`azure-storage-file-datalake`, Terraform with `microsoft/fabric` 1.14.x and `azurerm` 4.x, GitHub
Actions with `azure/login@v2` (OIDC).

**Spec:** `docs/superpowers/specs/2026-10-21-fabric-lakehouse-design.md` (approved 21 Oct 2026).
**Research this plan relies on (22 Oct 2026, sources inline):** a local research file, copied into
the repo as `docs/research/2026-10-22-fabric-facts.md` in Task 1 (the controller gives its path).

**Plan 3** (written after this merges) is the live runbook, one owner yes per outward step.

## Decisions this plan makes (each recorded in the spec's amendments section by Task 1)

1. **Trial capacity is not supported by the Terraform provider.** Terraform creates the two
   workspaces (with workspace identity) and their role assignments without `capacity_id`; the owner
   assigns the trial capacity once, in the UI. *Rejected: dropping Terraform* — workspaces and roles
   as code is still the skill being shown.
2. **Lakehouse shortcut tracking is off** (`alm.settings.json`). A shortcut in git serialises the
   storage URL, which the owner ruled out (spec 6), and "Update from Git" deletes untracked
   shortcuts. The deploy creates the shortcut through the REST API from a secret.
3. **The wheel goes to `Files/wheels/`, not into an Environment item.** Environment publishes with a
   custom wheel can take 20+ minutes in `fabric-cicd`, and the wheel would have to be committed to
   git for dev. Notebooks pass `_inlineInstallationEnabled` so `%pip` works in pipeline runs.
   *Rejected: Environment item* — slower, binary in git. The README states the trade-off.
4. **Schema-enabled lakehouse; layers are schemas.** `Tables/` allows only top-level tables in a
   non-schema lakehouse, so `Tables/silver/x` needs schemas; it also reads naturally as medallion.
5. **The cost export is CSV with gzip, read through its `manifest.json`.** Each run's manifest lists
   its part files and `dataRowCount`, so silver can prove no row was dropped against the source,
   not against itself.
6. **Cost-export storage: hierarchical namespace on, shared keys off, Entra-only access, public
   network access enabled.** The trial cannot use trusted workspace access to a firewalled account.
   This is an owner decision at plan 3's storage step; plan 3 stops there if the owner refuses.

## Global Constraints

- Everything from plan 1's Global Constraints still holds (identity, no identifiers in committed
  files, no private data, as-is bronze, re-runs change nothing, nothing dropped, own currency).
- No Azure, Fabric or GitHub calls in this plan. Terraform: `fmt -check` and `validate` with
  `init -backend=false` only; never `plan` or `apply`.
- Fabric IDs (workspace, item, logical IDs) may be committed. Tenant and subscription IDs, storage
  account names, endpoint URLs and the cost-export location may not; they come from GitHub
  environment secrets or Terraform variables at run time.
- `fabric-cicd` is pinned to `1.4.0` and authenticates only with `AzureCliCredential()` after
  `azure/login` (OIDC). No client secrets anywhere.
- Fabric item files follow Microsoft's git formats as documented in the research file; anything not
  documented there is marked `# verify in plan 3` in a comment, never guessed silently.
- Deploy workflows run only from `main` (prod) or `dev` (dev wheel); never on `pull_request`.
- Commit after every task; no push.

## Review Focus

1. **The same code path locally and in Fabric.** A path built from `LakePaths` must be valid for
   Spark (`abfss://…` or relative `Tables/…`) and for Python file APIs (the `/lakehouse/default`
   mount) without the caller knowing which is which. *(Tests in Tasks 2 and 6.)*
2. **A cost export with several run folders for one month.** Only the latest run per month folder
   is read; an older run's rows are never added to a newer one's. *(Test in Task 4.)*
3. **A changed seed or package re-running the same load date.** The run is recorded as a new
   baseline with its inputs digest, not failed for ever. *(Test in Task 5.)*
4. **A deploy where the lakehouse already exists in prod.** Re-running the deploy is a no-op on
   items and only replaces the wheel and the shortcut. *(Test in Task 8, against fakes.)*
5. **A notebook file that carries a forbidden value or an output.** The hygiene check rejects it,
   and rejects any `shortcuts.metadata.json` with a non-empty array. *(Test in Task 7.)*

---

## File structure (new or changed)

```
docs/research/2026-10-22-fabric-facts.md     the research, copied in (Task 1)
src/lakehouse/paths.py                       LakePaths(tables_root, files_root, files_mount) (Task 2)
src/lakehouse/seeds/                         seeds move here as package data (Task 3)
src/lakehouse/seeding.py                     read_seed_rows / seed_df: Python-read seeds (Task 3)
src/lakehouse/bronze/fetch_public.py         writer callable, traversal guard, URL encoding (Task 3)
src/lakehouse/silver/costs.py                alias map, manifest-based export reader (Task 4)
src/lakehouse/run.py                         inputs digest in run_log; paths from LakePaths (Task 5)
src/lakehouse/fabric_entry.py                notebook entry points (Task 6)
fabric/                                      fabric-cicd repository_directory (Task 7)
  lh_portfolio.Lakehouse/                    .platform, lakehouse.metadata.json, alm.settings.json
  nb_ingest_public.Notebook/                 .platform, notebook-content.py
  nb_load.Notebook/                          .platform, notebook-content.py
  pl_daily_load.DataPipeline/                .platform, pipeline-content.json, .schedules
  parameter.yml                              per-environment replacements
.deploy/deploy.py                            wheel upload, fabric-cicd publish, shortcut (Task 8)
.github/workflows/ci.yml                     hardened (Task 9)
.github/workflows/deploy.yml                 dev wheel on push to dev; prod on push to main (Task 9)
infra/fabric/                                workspaces, identities, roles (Task 10)
infra/cost-export/                           storage account and container (Task 10)
tools/check_hygiene.py                       Fabric rules added (Task 7)
```

---

### Task 1: Record the research and the amendments

**Files:**
- Create: `docs/research/2026-10-22-fabric-facts.md` (copy of the research file, unchanged)
- Modify: `docs/superpowers/specs/2026-10-21-fabric-lakehouse-design.md` (append section
  `## 11. Amendments, 22 October 2026`)

- [ ] **Step 1:** Copy the research file into `docs/research/`. Grep it for GUIDs, `https://<acct>`
  style placeholders are fine, real account names are not; expected: no identifiers (it was written
  from public docs only).
- [ ] **Step 2:** Append the six decisions above to the spec, each as one paragraph with its
  rejected option, plus: the cost-export column names are the pay-as-you-go 2019-11-01 schema
  (`UsageDateTime`, `InstanceId`, `ResourceGroup`, `MeterId`, `MeterName`, `PreTaxCost`, `Currency`,
  `Tags`), aliased to the newer names; and the Cost Management caveat that costs before an offer
  change may be unavailable, so September may not export (spec 3.5 already covers shrinking the
  window).
- [ ] **Step 3:** Run `python tools/check_hygiene.py`. Expected: clean.
- [ ] **Step 4: Commit.** `git commit -m "Record Fabric research and plan 2 amendments to the spec"`

---

### Task 2: `LakePaths` for Fabric's `Tables/` and `Files/`

**Files:**
- Modify: `src/lakehouse/paths.py`, every caller in `src/` and `tests/` (grep `LakePaths(` and
  `.bronze`), `tests/conftest.py`
- Test: `tests/test_paths.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) LakePaths(tables_root: str, files_root: str, files_mount: str |
  None = None)`.
  - `table(layer: str, name: str) -> str` = `f"{tables_root}/{layer}/{name}"`; `layer` must be one
    of `"silver"`, `"gold"`, `"reject"` (`ValueError` otherwise).
  - `bronze -> str` = `f"{files_root}/bronze"` (Spark path).
  - `bronze_local -> str` = `f"{files_mount or files_root}/bronze"` (Python file-API path).
  - `private -> str` = `f"{files_root}/bronze/private"`.
  - `@classmethod local(root: str) -> LakePaths` = `LakePaths(f"{root}/Tables", f"{root}/Files")`.
- The `tmp_lake` fixture becomes `LakePaths.local(str(tmp_path))`.
- `run_load` drops its `private_root` parameter and reads `paths.private` (plan 3 uploads the
  extracts to `Files/bronze/private/`).

- [ ] **Step 1: Write the failing tests:** `test_table_paths_are_schema_style`
  (`table("gold","fact_daily_cost")` → `".../Tables/gold/fact_daily_cost"`),
  `test_unknown_layer_raises`, `test_bronze_local_uses_mount_when_given`
  (`files_root="Files"`, `files_mount="/lakehouse/default/Files"` → `bronze == "Files/bronze"`,
  `bronze_local == "/lakehouse/default/Files/bronze"`), `test_local_layout`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement**, then update every caller. `fetch_manifest` callers pass
  `paths.bronze_local`; Spark readers keep `paths.bronze`.
- [ ] **Step 4: Run the full suite.** Expected: all pass (same count as before plus the new tests).
- [ ] **Step 5: Commit.** `git commit -m "Split LakePaths into Tables and Files roots"`

---

### Task 3: Packaged seeds read in Python; `fetch_manifest` writer

**Files:**
- Move: `seeds/*` → `src/lakehouse/seeds/*` (`git mv`); update `pyproject.toml` package data,
  `tools/verify_deployment_seed.py`, every `seeds_dir` default
- Create: `src/lakehouse/seeding.py`
- Modify: `src/lakehouse/bronze/fetch_public.py`, `src/lakehouse/silver/eval_attempts.py`,
  `silver/deployments.py`, `attribution.py`, `checks.py`, `gold/build.py`
- Test: `tests/test_seeding.py`, `tests/test_fetch_public.py`

**Interfaces:**
- Produces: `SEEDS_DIR: str` = the packaged folder, from
  `str(importlib.resources.files("lakehouse") / "seeds")`; it replaces `deployments.SEEDS_DIR`.
- Produces: `read_seed_rows(seeds_dir: str, name: str) -> list[dict[str, str]]` (Python `csv`,
  UTF-8, raises `ValueError` naming the file if the header is missing or a row has the wrong field
  count) and `seed_df(spark, seeds_dir: str, name: str, schema: StructType) -> DataFrame`
  (`read_seed_rows`, header must equal `schema.fieldNames()` exactly, each value converted to the
  field's type with a `ValueError` naming file, row and column on failure, then
  `createDataFrame(rows, schema)`).
- Every seed read in the package goes through these two functions. Spark never reads a seed path.
- `fetch_manifest(manifest, bronze_root, load_date, http_get, write: Callable[[str, bytes], None] |
  None = None) -> list[str]`: `write` defaults to a local `pathlib` writer; each `path` is rejected
  (`ValueError`) if absolute or containing a `..` segment; the URL percent-encodes each path segment
  with `urllib.parse.quote`.

- [ ] **Step 1: Write the failing tests:** `test_seed_df_matches_schema`,
  `test_seed_df_bad_value_names_row_and_column`, `test_header_mismatch_raises`,
  `test_wheel_contains_seeds` (build the wheel into `tmp_path` with `python -m build --wheel`, open
  it with `zipfile`, assert every current seed CSV is present under `lakehouse/seeds/`),
  `test_fetch_uses_writer`, `test_fetch_rejects_traversal` (`"../x"` and `"/etc/x"`),
  `test_fetch_encodes_spaces`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement**, move the seeds, replace every Spark CSV read of a seed with `seed_df`,
  and every `open()` of a seed with `read_seed_rows`. Add `build` to the dev extras.
- [ ] **Step 4: Run the full suite** and `python tools/verify_deployment_seed.py --releaselens
  ../ReleaseLens --fwa ../foundry-workshop-agent`. Expected: all pass; the tool still verifies 12
  rows.
- [ ] **Step 5: Commit.** `git commit -m "Package seeds, read them in Python, add a fetch writer"`

---

### Task 4: The real cost export: aliases and the manifest

**Files:**
- Modify: `src/lakehouse/silver/costs.py`, `src/lakehouse/checks.py` (`check_cost_totals`),
  `src/lakehouse/run.py`
- Create: `tests/fixtures/cost_export/` (synthetic layout:
  `portfolio-daily/20261001-20261031/<run-a>/manifest.json` + `part0.csv.gz`, a second, later
  `<run-b>/` for the same month with one fewer row, and `20260901-20260930/<run-c>/`)
- Test: `tests/test_costs.py`, `tests/test_checks.py`

**Interfaces:**
- `COST_COLUMN_ALIASES: dict[str, tuple[str, ...]]` (silver name → accepted export headers, first
  match wins, case-insensitive): `cost_date` ← (`UsageDateTime`, `Date`); `resource_id` ←
  (`InstanceId`, `ResourceId`); `resource_group` ← (`ResourceGroup`, `ResourceGroupName`);
  `meter_id` ← (`MeterId`); `meter_name` ← (`MeterName`); `cost` ← (`PreTaxCost`,
  `CostInBillingCurrency`, `Cost`); `billing_currency` ← (`Currency`, `BillingCurrencyCode`,
  `BillingCurrency`); `tags` ← (`Tags`). A header set that matches none for a silver column raises
  `ValueError` listing the headers found. This replaces `COST_COLUMN_MAP` (R12 in plan 1's ledger).
- `@dataclass(frozen=True) ExportRun(month_folder: str, run_id: str, submitted: datetime, blobs:
  tuple[str, ...], data_row_count: int)`.
- `find_latest_runs(spark, export_root: str) -> list[ExportRun]`: reads every `manifest.json` under
  `export_root` (Spark `text` read with `wholetext`, parsed in Python), groups by month folder
  (`YYYYMMDD-YYYYMMDD`), keeps the run with the latest `runInfo.submittedTime`, blob paths resolved
  relative to `export_root`. Raises `FileNotFoundError` naming `export_root` if no manifest is found.
- `read_cost_export(spark, export_root: str) -> tuple[DataFrame, dict[str, int]]`: reads only the
  latest runs' blobs (`csv`, gzip by extension, FAILFAST), and returns the export rows plus
  `{month_folder: data_row_count}` from the manifests.
- `check_cost_totals` gains `expected_rows: dict[str, int]` and fails when the silver row count for
  a month folder's range differs from the manifest's `dataRowCount`, in addition to the existing
  total comparison. Its detail names the month and both counts.
- `run_load(..., cost_export_root: str, ...)` replaces `cost_export_path`.

- [ ] **Step 1: Write the failing tests:** `test_mosa_headers_resolve`,
  `test_new_headers_resolve`, `test_unknown_headers_raise_listing_found`,
  `test_latest_run_per_month_only` *(Review Focus 2)*: run-b's rows, not run-a's, for October;
  September's run-c also read; `test_manifest_row_count_mismatch_fails_check` (delete one row from
  a fixture part file's copy in `tmp_path` → `cost_totals` fails naming both counts),
  `test_no_manifest_raises`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.** Build the fixture with a small script under `tests/fixtures/` that is
  itself committed (`make_cost_export_fixture.py`), so the gzip files are reproducible.
- [ ] **Step 4: Run the full suite.** Expected: all pass.
- [ ] **Step 5: Commit.** `git commit -m "Read the cost export through its manifest, accept both header schemas"`

---

### Task 5: Inputs digest in the run log

**Files:**
- Modify: `src/lakehouse/run.py`, `tests/test_run.py`

**Interfaces:**
- `inputs_digest(seeds_dir: str, manifest: dict, export_runs: list[ExportRun]) -> str`: sha256 over
  the package version (`importlib.metadata.version("lakehouse")`), every seed file's bytes in name
  order, the ingest manifest JSON (sorted keys), and the export runs' `run_id`s. First 16 hex chars.
- `silver/run_log` gains `inputs_digest` and `status` (`"passed"`). The rerun check compares with
  the latest passed entry for the same `load_date` **and** the same `inputs_digest`; if none exists,
  the run is a new baseline and the check passes with detail `"new baseline: inputs changed"` or
  `"first run for this load date"`.
- The earlier-date guard (plan 1 fix wave) compares with the latest `load_date` of any passed entry.

- [ ] **Step 1: Write the failing tests:** `test_changed_seed_is_new_baseline`
  *(Review Focus 3)*, `test_same_inputs_still_compared`, `test_digest_changes_with_export_run`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.** Existing `run_log` rows without the new columns are read as
  `inputs_digest = null`, which never matches.
- [ ] **Step 4: Run** `tests/test_run.py` and the full suite. Expected: all pass.
- [ ] **Step 5: Commit.** `git commit -m "Record an inputs digest so changed inputs start a new baseline"`

---

### Task 6: Notebook entry points

**Files:**
- Create: `src/lakehouse/fabric_entry.py`, `tests/test_fabric_entry.py`

**Interfaces:**
- `fabric_paths(context: dict) -> LakePaths`: from a runtime context with keys
  `currentWorkspaceId` and `defaultLakehouseId` (the shape of `notebookutils.runtime.context`;
  `# verify in plan 3`), returns
  `LakePaths(tables_root=f"abfss://{ws}@onelake.dfs.fabric.microsoft.com/{lh}/Tables",
  files_root=f"abfss://{ws}@onelake.dfs.fabric.microsoft.com/{lh}/Files",
  files_mount="/lakehouse/default/Files")`. Raises `ValueError` naming the missing key.
- `ingest_public(load_date: str, context: dict, http_get=None) -> int`: parses the date, loads the
  packaged ingest manifest, calls `fetch_manifest` with `write` = local writer under
  `paths.bronze_local`, and `http_get` defaulting to a `requests`-based getter that checks the
  status before the body. Returns the file count.
- `load(load_date: str, context: dict, cost_export_dir: str = "Files/cost_export") -> dict`: builds
  `fabric_paths`, calls `run_load(get_active_spark(), paths, SEEDS_DIR,
  cost_export_root=f"{paths.files_root}/{cost_export_dir.removeprefix('Files/')}", load_date=...)`
  and returns `RunSummary` as a JSON-able dict. Any `QualityCheckFailed` propagates (the pipeline
  activity must fail).
- `get_active_spark()` returns `SparkSession.getActiveSession()` or raises if none.

- [ ] **Step 1: Write the failing tests:** `test_fabric_paths_from_context`,
  `test_missing_context_key_raises`, `test_ingest_public_writes_under_mount` (fake context with
  `files_mount` patched to `tmp_path`, fake `http_get`), `test_load_propagates_quality_failure`
  (monkeypatch `run_load` to raise `QualityCheckFailed`), `test_load_returns_jsonable_summary`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.** Add `requests` to the runtime dependencies.
- [ ] **Step 4: Run.** Expected: pass.
- [ ] **Step 5: Commit.** `git commit -m "Add Fabric notebook entry points"`

---

### Task 7: Fabric item definitions and hygiene rules

**Files:**
- Create: everything under `fabric/` (see file structure), `tests/test_fabric_items.py`
- Modify: `tools/check_hygiene.py`, `tests/test_hygiene.py`

**Interfaces and content (the implementer reads the research file §3, §8, §9 and Microsoft's item
definition docs it cites; anything not covered there gets `# verify in plan 3`):**
- `lh_portfolio.Lakehouse/`: `.platform` (type `Lakehouse`, displayName `lh_portfolio`, a new
  logicalId GUID), `lakehouse.metadata.json` = `{"defaultSchema": "dbo"}`, `alm.settings.json` with
  shortcut tracking disabled for every shortcut sub-type it supports.
- `nb_ingest_public.Notebook/notebook-content.py` and `nb_load.Notebook/notebook-content.py`:
  - default lakehouse = `lh_portfolio` (its logicalId and an empty-GUID workspace, the git form
    for "this workspace");
  - a parameter cell with `load_date = ""` and `wheel = "lakehouse-<version>-py3-none-any.whl"`;
  - a cell `%pip install /lakehouse/default/Files/wheels/{wheel}` (`# verify in plan 3` whether
    `%pip` expands the variable; else use `get_ipython().run_line_magic`);
  - a cell that calls `lakehouse.fabric_entry.ingest_public` / `.load` with
    `notebookutils.runtime.context`, and prints the returned summary.
  - No outputs, no workspace or tenant IDs beyond the empty GUID.
- `pl_daily_load.DataPipeline/pipeline-content.json`: parameter `load_date` (String, default
  empty); two Notebook activities, `ingest` → `load` on success, each with base parameters
  `load_date` = `@if(empty(pipeline().parameters.load_date), formatDateTime(pipeline().TriggerTime,
  'yyyy-MM-dd'), pipeline().parameters.load_date)`, `wheel`, and `_inlineInstallationEnabled` =
  `true` (Bool); policy retry `2`, retry interval `60` s, timeout `0.01:00:00`.
  `.schedules`: daily at 06:00 Australia/Brisbane, start today, end `2099-01-01`, `enabled: false`.
- `parameter.yml` (`fabric-cicd` format, research §4):
  - `find_replace`: the notebooks' default-lakehouse logicalId → `$items.Lakehouse.lh_portfolio.$id`,
    and the empty workspace GUID → `$workspace.$id`, for `PROD`; regexes per the research's
    `# META "default_lakehouse"` example.
  - `key_value_replace`: `$.schedules[?(@.jobType=="Execute")].enabled` → `PROD: true`.
- Hygiene additions to `check_hygiene`: reject any `shortcuts.metadata.json` whose JSON is a
  non-empty array; reject any `notebook-content.py` containing a line starting `# OUTPUT` or
  `# CELL OUTPUT` (`# verify in plan 3` against a real committed notebook); keep the forbidden-value
  rule for every file under `fabric/`.
- `tests/test_fabric_items.py`: every `.platform` parses and has a unique logicalId; the notebook
  files contain a parameter cell and no forbidden patterns; `pipeline-content.json` parses, has
  exactly two Notebook activities in the stated order with retry 2; `parameter.yml` parses and
  every `$items.<type>.<name>` it names exists under `fabric/`; the lakehouse has
  `defaultSchema`.

- [ ] **Step 1: Write the failing tests** (the list above, plus hygiene tests
  `test_flags_nonempty_shortcuts_file`, `test_allows_empty_shortcuts_file`,
  `test_flags_notebook_output_marker` *(Review Focus 5)*).
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Write the item files** and the hygiene rules.
- [ ] **Step 4: Run** the two test files and the full suite. Expected: all pass.
- [ ] **Step 5: Commit.** `git commit -m "Add Fabric lakehouse, notebooks, pipeline and parameter file"`

---

### Task 8: The deploy script

**Files:**
- Create: `.deploy/deploy.py`, `tests/test_deploy.py`
- Modify: `pyproject.toml` (a `deploy` extra: `fabric-cicd==1.4.0`, `azure-identity`,
  `azure-storage-file-datalake`)

**Interfaces:**
- `main(argv: list[str] | None = None) -> int` with subcommands, every input from environment
  variables (never arguments that would land in logs):
  - `upload-wheel` — env `FABRIC_WORKSPACE_ID`, `FABRIC_LAKEHOUSE_ID`, `WHEEL_PATH`: uploads to
    `https://onelake.dfs.fabric.microsoft.com`, file system = workspace ID, path
    `{lakehouse_id}/Files/wheels/{wheel_name}`, overwrite, with `AzureCliCredential`.
  - `publish` — env `FABRIC_WORKSPACE_ID`, `FABRIC_ENVIRONMENT` (`PROD`): `FabricWorkspace(
    workspace_id=..., environment=..., repository_directory="fabric",
    item_type_in_scope=["Lakehouse", "Notebook", "DataPipeline"],
    token_credential=AzureCliCredential())`, then `publish_all_items(ws)`; no unpublish.
  - `ensure-shortcut` — env `FABRIC_WORKSPACE_ID`, `FABRIC_LAKEHOUSE_ID`, `FABRIC_CONNECTION_ID`,
    `COST_EXPORT_LOCATION` (secret), `COST_EXPORT_SUBPATH`: `POST
    /v1/workspaces/{ws}/items/{lh}/shortcuts?shortcutConflictPolicy=CreateOrOverwrite` with
    `{"path": "Files", "name": "cost_export", "target": {"adlsGen2": {"location", "subpath",
    "connectionId"}}}` (research §7). Reads the status before the body; on non-2xx prints the
    status and the response's `errorCode` only, never the request body.
- Seams for tests: `_credential()`, `_datalake_client(credential)`, `_http_post(url, json, token)`,
  `_fabric_workspace(**kwargs)` / `_publish(ws)` are module-level functions the tests monkeypatch.
- No secret value is ever printed; a test asserts that captured stdout and stderr contain none of
  the env values.

- [ ] **Step 1: Write the failing tests:** `test_upload_wheel_path`, `test_publish_scope_and_args`,
  `test_ensure_shortcut_payload`, `test_ensure_shortcut_is_idempotent_policy` *(Review Focus 4)*
  (the URL carries `CreateOrOverwrite`), `test_non_2xx_prints_status_not_body`,
  `test_missing_env_names_variable_only`, `test_no_secret_in_output`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: pass.
- [ ] **Step 5: Commit.** `git commit -m "Add the deploy script: wheel upload, fabric-cicd publish, shortcut"`

---

### Task 9: CI hardening and the deploy workflow

**Files:**
- Modify: `.github/workflows/ci.yml`
- Create: `.github/workflows/deploy.yml`, `tests/test_workflows.py`

**Content:**
- `ci.yml`: `timeout-minutes: 45`; `actions/setup-python` with `cache: pip`; cache `~/.ivy2` keyed on
  `pyproject.toml`; pytest; `python -m build --wheel` as a check; hygiene step with `if: always()`;
  the hygiene step fails when `HYGIENE_FORBIDDEN` is empty on a `push` event (a PR from a fork has
  no secrets, so the check is skipped there with a notice); `terraform fmt -check -recursive infra`
  and `terraform -chdir=infra/<stack> init -backend=false && terraform validate` for both stacks.
  Actions pinned to full commit SHAs with the tag in a comment.
- `deploy.yml`: `on: push` to `dev` and `main` only (no `pull_request`, no `workflow_dispatch`
  inputs that take values).
  - job `build`: builds the wheel, uploads it as an artifact. `permissions: contents: read`.
  - job `dev` (if `refs/heads/dev`): `environment: dev`, `permissions: id-token: write, contents:
    read`, `azure/login@v2` with `client-id`, `tenant-id` from environment secrets and
    `allow-no-subscriptions: true`; `deploy.py upload-wheel` then `ensure-shortcut`. No `publish`:
    dev items come from git sync.
  - job `prod` (if `refs/heads/main`): `environment: prod` (required reviewer is configured in plan
    3), same login, then `upload-wheel`, `publish`, `ensure-shortcut`.
  - `concurrency: deploy-${{ github.ref }}`; `timeout-minutes: 30`.
- `tests/test_workflows.py` (PyYAML): `deploy.yml` has no `pull_request` trigger; every job that
  logs in has `id-token: write` and an `environment`; `prod` runs only on `main`; no step echoes a
  secret; every `uses:` is pinned to a 40-hex SHA.

- [ ] **Step 1: Write the failing tests.**
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Write the workflows.** Look up each action's current release SHA from its GitHub
  releases page (read-only), and record the tag beside it.
- [ ] **Step 4: Run.** Expected: pass.
- [ ] **Step 5: Commit.** `git commit -m "Harden CI and add the deploy workflow"`

---

### Task 10: Terraform for the workspaces and the cost-export storage

**Files:**
- Create: `infra/fabric/{main.tf,variables.tf,outputs.tf,versions.tf}`,
  `infra/cost-export/{main.tf,variables.tf,outputs.tf,versions.tf}`,
  `infra/README.md` (one paragraph per stack: what it creates, which values come from
  `terraform.tfvars`, state is local and git-ignored)
- Modify: `.gitignore` (`*.tfvars`, `.terraform/`, `*.tfstate*`, `*.tfplan`),
  `src/lakehouse/seeds/rg_rules.csv` (add `rg-p9-*,p9,...`), `src/lakehouse/gold/build.py`
  (`PROJECTS` gains `p9`, "Fabric lakehouse and batch pipelines")

**Content:**
- `infra/fabric`: provider `microsoft/fabric` `~> 1.14`, Azure CLI auth (user context). Two
  `fabric_workspace` resources (`p9-dev`, `p9-prod` display names; `identity { type =
  "SystemAssigned" }`; no `capacity_id`, with a comment citing the trial limitation);
  `fabric_workspace_role_assignment` for the owner (Admin, `var.owner_object_id`) and the deploy
  service principal (Contributor on both, `var.deploy_sp_object_id`). Outputs: workspace IDs and
  each workspace identity's `service_principal_id`.
- `infra/cost-export`: provider `azurerm` `~> 4.0`. Resource group `rg-p9-cost-export` (tag
  `project = "p9"`), storage account (`var.storage_account_name`, StorageV2, LRS,
  `is_hns_enabled = true`, `shared_access_key_enabled = false`,
  `public_network_access_enabled = true` with a comment pointing to spec amendment 6,
  `min_tls_version = "TLS1_2"`, `allow_nested_items_to_be_public = false`), container
  `cost-exports`, and `azurerm_role_assignment` "Storage Blob Data Reader" at account scope for
  each principal in `var.reader_principal_ids` (the two workspace identities). Output: none that
  names the account.
- `tests/test_infra.py`: parses nothing with HCL libraries; asserts by text that no `.tf` file
  contains a GUID, an `https://` URL, or a literal storage account name, and that both stacks pin
  their provider versions.

- [ ] **Step 1: Write the failing tests** (`tests/test_infra.py`, and a gold test that `dim_project`
  has `p9`, an attribution test that `rg-p9-cost-export` → `p9`).
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Write both stacks**, the seed row and the project row.
- [ ] **Step 4: Run** `terraform fmt -check -recursive infra`, `init -backend=false` and `validate`
  for both stacks (in WSL; install Terraform there if absent, with the Norton bundle per command),
  then the full suite. Expected: all pass.
- [ ] **Step 5: Commit.** `git commit -m "Add Terraform for Fabric workspaces and the cost-export storage"`

---

## After plan 2

- Update the tracker log in `portfolio-plan` (approved push).
- Write plan 3, the live runbook. Its order, each step behind the owner's yes:
  1. Read-only preflight: `az account show` (personal tenant), the budget alert, whether the
     ReleaseLens and Foundry bootstrap stacks still exist.
  2. Cost-export storage (`infra/cost-export` apply) and the export itself (one-time for
     September and October, then daily) — first, because exports take up to a day and September
     may not exist. The owner decides on amendment 6 here.
  3. Fabric sign-in as the personal-tenant user, tenant settings, start the trial (the 60 days
     begin), record F4 or F64.
  4. The history decision on the old spec wording, then create the public GitHub repo, set the
     hygiene secret, push `main` and `dev`, protect `main`.
  5. The Entra app registration and federated credentials for the `dev` and `prod` environments.
  6. `infra/fabric` apply; assign the trial capacity to both workspaces; workspace-identity
     connections to the storage.
  7. Connect the dev workspace to `dev` with a fine-grained PAT; update from git.
  8. Upload the private extracts; run the dev pipeline; break each quality check on purpose in dev
     and keep the failed runs.
  9. PR `dev` → `main`, CI, approve the prod deploy, run prod, backfill.
  10. Capacity Metrics, the dated answers in the README, the push, the tracker.
