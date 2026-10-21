# Project 9, plan 1: offline lakehouse code — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and test, with no Azure and no Fabric, every transform, seed, check and tool that
plan 2 will deploy to Fabric: the private extractor, bronze ingest, silver `MERGE`s, the Type 2
deployment dimension, cost attribution, the gold star schema, the quality checks, and one
`run_load` entry point that a Fabric notebook will call.

**Architecture:** A Python package, `lakehouse`, under `src/`. Each Fabric notebook in plan 2 is a
thin wrapper that calls `lakehouse.run.run_load(...)`. All logic is here, unit-tested with pytest
against a local Spark with Delta, on synthetic fixtures. Seeds (hand-curated, evidence-cited CSVs)
live in `seeds/`. Paths are parameters, so the same code runs on a local folder and on OneLake.

**Tech Stack:** Python and PySpark with `delta-spark`, pinned to the current Fabric runtime (see
Task 1), pytest, GitHub Actions on `ubuntu-latest`. Local Spark runs in WSL Ubuntu, because this
PC's Windows Python is 3.14 with Java 8.

**Spec:** `docs/superpowers/specs/2026-10-21-fabric-lakehouse-design.md` (approved 21 Oct 2026).

**Plan 2** (written after this one merges) covers the live work: the trial, workspaces, git
integration, the Fabric environment and notebooks, the pipeline, the cost export and shortcut,
the private upload, the service principal and the GitHub Actions deploy, the deliberate check
failures, capacity measurement, and the README's dated answers.

## Global Constraints

- Commits as `Dmitry Lyubaev <Dmitry.Lyubaev@gmail.com>`; check `git log --format='%an <%ae>'`
  before any push. **No push and no GitHub repo creation without the owner's yes.**
- Nothing from the employer. No tenant or subscription IDs, storage account names or endpoint URLs
  in any committed file. Fabric workspace and item IDs may be committed (owner decision, 21 Oct).
- No private data in the repo: tests use synthetic fixtures only. Private source files are read
  only by `lakehouse.extract.private`, whose output goes to the git-ignored `private/` folder.
- Every bronze landing is as-is, under a `load_date=YYYY-MM-DD` folder.
- Running the same load date twice changes no gold row.
- Unattributable spend goes to `untagged`; nothing is dropped. Unresolved results go to a reject
  table; nothing is dropped.
- Costs stay in their own currency: repo estimates in USD, Cost Management in its billing
  currency. Never convert with an invented rate.
- Norton inspects TLS on this PC: pip in WSL may need `--cert` with the CA bundle `az` already
  uses. Read HTTP status codes before bodies.
- Commit after every task; no push.

## Review Focus

1. **Cost tags in both export formats.** Cost Management writes tags either as a JSON object or as
   `"key": "value"` pairs without braces, and sometimes empty. All three must parse; empty means
   no tag, not an error. *(Test in Task 5.)*
2. **Timestamps with mixed offsets.** P1/P2 `started_at` carry `+00:00`, P3 `startedAt` carries
   `+00:00` with microseconds, git commit times carry `+10:00`. All are normalised to UTC before
   the Type 2 range join. *(Test in Task 7.)*
3. **A restated month that shrinks.** If a resource disappears from a later export of the same
   month, its old rows must be gone after the month is replaced. *(Test in Task 5.)*
4. **A retrieval target missing from all 50 hits.** The row is kept with `target_rank = null`,
   `top1 = false` and `reciprocal_rank = 0.0`. *(Test in Task 2.)*
5. **An empty or missing cost export for a load date.** The run fails loudly and names the missing
   path; it never writes an empty month over a full one. *(Test in Task 8.)*

---

## File structure

```
pyproject.toml                       package, pinned deps, pytest config
.gitignore                           private/, .local/, .env, *.tfstate*, .venv/, spark-warehouse/
.github/workflows/ci.yml             pytest + hygiene on ubuntu-latest
tools/check_hygiene.py               rejects notebook outputs and forbidden literal values
tools/verify_deployment_seed.py      local only: checks seed rows against git history
src/lakehouse/
  spark.py                           get_spark(): local Delta-enabled session (tests, local runs)
  paths.py                           LakePaths: bronze/silver/gold/reject paths from one root
  extract/private.py                 P1 + P2 sanitised extracts (CLI)
  bronze/fetch_public.py             fetch public files pinned to a commit SHA
  silver/eval_attempts.py            EVAL_COLUMNS, P1/P2/P3 → one schema, cost estimate, MERGE
  silver/deployments.py              Type 2 dimension via MERGE
  silver/costs.py                    export normalisation, month replace
  attribution.py                     cost → project rules
  gold/build.py                      dims, facts, reject table
  gold/fingerprint.py                content hash per gold table (re-run check)
  checks.py                          the four quality checks, gold fingerprint
  answers.py                         the three gold answers as DataFrames
  run.py                             run_load(): bronze → silver → gold → checks
seeds/
  ingest_manifest.json               public files to fetch, each pinned to a SHA
  dim_arm.csv                        study, arm → deployment key / external model / none
  deployment_changes.csv             Type 2 source rows, each citing its commit or evidence
  model_rates.csv                    USD per 1M tokens, with date and source
  rg_rules.csv                       resource-group rules
  session_windows.csv                project session dates for shared resources
  live_checks.csv                    P4–P8 checks transcribed from public reports
  published_figures.csv              figures gold must reproduce
tests/                               one test file per module, fixtures under tests/fixtures/
```

---

### Task 1: Scaffold, local Spark in WSL, CI and the hygiene check

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `.github/workflows/ci.yml`, `src/lakehouse/__init__.py`,
  `src/lakehouse/spark.py`, `src/lakehouse/paths.py`, `tools/check_hygiene.py`,
  `tests/conftest.py`, `tests/test_spark_smoke.py`, `tests/test_hygiene.py`, `README.md` (one
  paragraph placeholder: name, one line on purpose, "work in progress")

**Interfaces:**
- Produces: `get_spark(app_name: str = "lakehouse-tests") -> SparkSession` (Delta-enabled, local[2],
  `spark.sql.session.timeZone=UTC`); pytest fixture `spark` (session-scoped) and `tmp_lake`
  (a `LakePaths` rooted in `tmp_path`).
- Produces: `@dataclass(frozen=True) LakePaths(root: str)` with properties `bronze`, `silver`,
  `gold`, `reject` (each `f"{root}/<layer>"`) and `table(layer: str, name: str) -> str`.
- Produces: `check_hygiene(paths: list[Path], forbidden: list[str]) -> list[str]` (problems found);
  CLI `python tools/check_hygiene.py` reads `HYGIENE_FORBIDDEN` (comma-separated) from the
  environment and scans tracked files.

- [ ] **Step 1: Pin the runtime.** Read Microsoft's "Apache Spark runtimes in Fabric" page and
  record the current GA runtime's Spark, Delta, Python and Java versions in `pyproject.toml`
  comments. Expected at the time of writing: Runtime 1.3 = Spark 3.5, Delta 3.2, Python 3.11,
  Java 11. Pin `pyspark` and `delta-spark` to those minor versions; dev deps `pytest`,
  `pytest-cov`.
- [ ] **Step 2: Set up WSL.** In WSL Ubuntu: install the pinned Java (OpenJDK) and Python, create
  `.venv` in `/mnt/e/Projects/Fabric`, `pip install -e .[dev]` (with `--cert` if Norton blocks).
  Expected: `python -c "import pyspark, delta"` succeeds.
- [ ] **Step 3: Write the failing tests.**
  - `test_spark_smoke.py::test_delta_round_trip`: write a 2-row DataFrame as Delta to
    `tmp_lake.table("silver","t")`, read it back, `assert count == 2` and
    `spark.conf.get("spark.sql.session.timeZone") == "UTC"`.
  - `test_hygiene.py::test_flags_notebook_outputs`: an `.ipynb` with a non-empty `outputs` list is
    reported; one with `outputs: []` is not.
  - `test_hygiene.py::test_flags_forbidden_values`: a file containing `"abc-secret-value"` with
    `forbidden=["abc-secret-value"]` is reported; the same file with `forbidden=[]` is not.
  - `test_hygiene.py::test_ignores_guid_patterns`: a file containing a random GUID is not reported
    (Fabric item IDs are allowed).
- [ ] **Step 4: Run them.** `pytest -q` in WSL. Expected: FAIL (modules missing).
- [ ] **Step 5: Implement** `spark.py` (`configure_spark_with_delta_pip`), `paths.py`, `conftest.py`
  and `tools/check_hygiene.py`. The hygiene check also reports any `.py` file under a
  Fabric item folder whose cells contain `# OUTPUT` blocks, if that format appears in plan 2.
- [ ] **Step 6: Run them.** `pytest -q`. Expected: all pass.
- [ ] **Step 7: Write CI.** `ci.yml` on `push` and `pull_request` to `main` and `dev`: setup Java
  and Python at the pinned versions, `pip install -e .[dev]`, `pytest -q`, then
  `python tools/check_hygiene.py` with `HYGIENE_FORBIDDEN: ${{ secrets.HYGIENE_FORBIDDEN }}`.
  `permissions: contents: read`. No Azure login in this workflow.
- [ ] **Step 8: Commit.** `git add -A && git commit -m "Scaffold lakehouse package, local Spark, CI and hygiene check"`

---

### Task 2: Private extractor (P1 and P2)

**Files:**
- Create: `src/lakehouse/extract/__init__.py`, `src/lakehouse/extract/private.py`,
  `tests/test_extract_private.py`, `tests/fixtures/p1_report.json`, `tests/fixtures/p2_report.json`,
  `tests/fixtures/p2_questions.jsonl` (synthetic: 2 queries × 3 arms × 2 passes for P1; 3
  questions × 2 arms plus 1 repeat for P2)

**Interfaces:**
- Produces: `extract_p1(report: dict) -> list[dict]`, `extract_p2(report: dict, questions:
  list[dict]) -> list[dict]`, `assert_clean(rows: list[dict]) -> None` (raises `ValueError`
  naming the field), CLI `python -m lakehouse.extract.private --p1 PATH --p2 PATH --questions PATH
  --out DIR` writing `DIR/p1_outcomes.jsonl` and `DIR/p2_results.jsonl`.
- P1 row keys, exactly: `study="p1-three-arm"`, `run_id`, `started_at` (run-level), `item_id`
  (outcome `id`), `arm`, `pass_index`, `attempt_kind="scored"`, `provider`, `model`, `success`
  (`error is None and not no_reply`), `latency_ms`, `tokens_in`, `tokens_out`, `cached_tokens`
  (`cache_read_input_tokens`), `cost_usd`, `judge_cost_usd`, `groundedness`, `citation_recall`,
  `citation_precision`, `must_contain` (`must_contain_satisfied`).
- P2 row keys, exactly: `study="p2-retrieval"`, `run_id`, `started_at` (run-level), `item_id`
  (`qid`), `arm`, `pass_index=1`, `attempt_kind` (`"scored"` for `results`,
  `"determinism_repeat"` for `repeat`), `success` (`error is None`), `latency_ms` (`ms`),
  `tokens_in` (`query_tokens`), `target_rank` (1-based rank of the first hit whose `artefact`
  equals the question's `target`, else `None`), `top1` (`target_rank == 1`), `reciprocal_rank`
  (`1/target_rank`, else `0.0`).
- Never emitted: `question`, `answer`, `groundedness_reason`, `citations`, `hits`, `chunk_id`, and
  anything from P2's `search`, `deployments` or `questions` blocks.

- [ ] **Step 1: Write the failing tests.**
  - `test_p1_row_shape`: keys equal the P1 list above; `success` is `False` for an outcome with
    `no_reply=True`.
  - `test_p2_rank_and_metrics`: target at hit 3 → `target_rank=3`, `top1=False`,
    `reciprocal_rank≈0.3333`.
  - `test_p2_target_missing_from_hits` *(Review Focus 4)*: `target_rank is None`, `top1 is False`,
    `reciprocal_rank == 0.0`, row present.
  - `test_p2_repeat_kind`: the repeat entry has `attempt_kind == "determinism_repeat"`.
  - `test_no_text_or_identifiers`: run both extracts, `assert_clean` passes, and no value matches
    a GUID regex or contains `https://`.
  - `test_assert_clean_rejects_answer`: a row with key `answer` raises `ValueError`.
- [ ] **Step 2: Run.** `pytest tests/test_extract_private.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `private.py`. `assert_clean` checks the forbidden key list and the GUID
  and URL patterns on every string value.
- [ ] **Step 4: Run.** Expected: all pass.
- [ ] **Step 5: Verify against the real private files (local only, prints aggregates only).** Run
  the CLI with `--p1 ../ReleaseLens/eval/reports/2c413fe9-*.json --p2
  ../ReleaseLens/eval/reports/retrieval-20261003T210637Z.json --questions
  ../ReleaseLens/eval/retrieval/questions.jsonl --out private/`. Then print, per P2 arm, the mean
  `top1` and mean `reciprocal_rank` over `scored` rows, next to the file's
  `analysis.arms[arm].top1` and `.mrr`. Expected: 90 P1 rows, 1,374 scored + 180 repeat P2 rows,
  and per-arm means equal to the file's within 0.0005. **If they differ, stop and report:** it
  means the published metric isn't artefact-match top-1, and Task 8's published figures change.
- [ ] **Step 6: Commit.** `git add src/lakehouse/extract tests/test_extract_private.py tests/fixtures && git commit -m "Add sanitised private extractor for P1 and P2"`
  (`private/` is git-ignored; confirm with `git status` that nothing under it is staged.)

---

### Task 3: Public ingest pinned to a commit

**Files:**
- Create: `src/lakehouse/bronze/__init__.py`, `src/lakehouse/bronze/fetch_public.py`,
  `seeds/ingest_manifest.json`, `tests/test_fetch_public.py`

**Interfaces:**
- Produces: `fetch_manifest(manifest: dict, bronze_root: str, load_date: date, http_get:
  Callable[[str], tuple[int, bytes]]) -> list[str]` (paths written). URL per file:
  `https://raw.githubusercontent.com/{owner}/{repo}/{sha}/{path}`. Destination:
  `{bronze_root}/public/load_date={YYYY-MM-DD}/{repo}/{path}`. Overwrites the same destination on
  re-run. Raises `RuntimeError(f"{status} for {repo}/{path}")` on any non-200, checking the status
  before reading the body.
- Manifest shape: `{"owner": "DmitryLyubaev", "sources": [{"repo": "...", "sha": "...", "paths":
  [...], "globs": [...]}]}`. Globs are expanded by the caller of plan 2 (the GitHub tree API); this
  task takes explicit `paths` only.
- Seed content: `foundry-workshop-agent` at `af31235` (full SHA from `git rev-parse`): the 120
  `sNN.<engine>.pN.json` files, `repeats/*.first-attempt.json` (9), `eval-scores.json`,
  `report.md`; `releaselens` at `origin/main`'s SHA: `eval/retrieval/questions.jsonl`,
  `eval/baseline.md`.

- [ ] **Step 1: Write the failing tests** with a fake `http_get`:
  - `test_writes_pinned_paths`: two files land under `load_date=2026-10-21/<repo>/<path>` with the
    exact bytes returned.
  - `test_rerun_overwrites_same_files`: running twice leaves the same file set and bytes.
  - `test_non_200_raises_without_reading_body`: a fake returning `(404, <sentinel that raises if
    touched>)` raises `RuntimeError` naming the path.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement** `fetch_public.py` and write `seeds/ingest_manifest.json`, generating
  the P3 file list with `git -C ../foundry-workshop-agent ls-tree -r --name-only <sha> -- results/study-2026-10-06-2601d16c644b`.
- [ ] **Step 4: Run.** Expected: pass.
- [ ] **Step 5: Commit.** `git commit -m "Add pinned public ingest and manifest"`

---

### Task 4: Silver eval attempts (one schema for P1, P2, P3) with `MERGE`

**Files:**
- Create: `src/lakehouse/silver/__init__.py`, `src/lakehouse/silver/eval_attempts.py`,
  `seeds/model_rates.csv`, `tests/test_eval_attempts.py`, `tests/fixtures/p3/` (4 synthetic run
  files, 1 first-attempt file, an `eval-scores.json`)

**Interfaces:**
- Consumes: Task 2 row dicts; Task 3 bronze layout.
- Produces: `EVAL_KEY = ["study", "item_id", "arm", "pass_index", "attempt_kind"]`;
  `EVAL_COLUMNS` (ordered): the key, `run_id`, `started_at` (timestamp, UTC), `provider`,
  `model`, `model_version`, `success`, `latency_ms`, `tokens_in`, `tokens_out`, `cached_tokens`,
  `cost_usd`, `cost_basis`, `groundedness`, `citation_recall`, `citation_precision`,
  `must_contain`, `top1`, `reciprocal_rank`, `target_rank`, `tool_call_accuracy`,
  `task_adherence`, `intent_resolution`, `load_date`.
- Produces: `parse_p3_run(run: dict, scores: dict | None, attempt_kind: str) -> dict` (study
  `"p3-agent"`, `item_id=scenarioId`, `arm=engine`, `pass_index=pass`, tokens summed over
  `calls`, `model_version = calls[0].modelId` or `None`, `success`, `latency_ms=ms`,
  `started_at=startedAt`, the three evaluator `score`s); main files are `"scored"`,
  `repeats/*.first-attempt.json` are `"superseded"`.
- Produces: `estimate_cost(model: str, tokens_in: int, tokens_out: int, rates: dict[str, tuple[float,
  float]]) -> float` (USD). P1 keeps its recorded `cost_usd + judge_cost_usd` with
  `cost_basis="recorded"`; P2 and P3 use the estimate with `cost_basis="tokens_x_rate"`.
- Produces: `build_eval_attempts(spark, bronze_root: str, private_root: str, load_date: date) ->
  DataFrame` and `merge_eval_attempts(spark, df: DataFrame, table_path: str) -> None` (Delta
  `MERGE` on `EVAL_KEY`: update when any non-key column differs, insert when not matched; creates
  the table if absent).
- `seeds/dim_arm.csv` (created here, consumed by Tasks 6 and 7) columns
  `study,arm,label,account_alias,deployment_name,model`: P1 `Z` → `rl-primary/releaselens-chat`,
  `A` → `external/claude-sonnet-5`, `O` → `external/gpt-4.1-mini@openai`; P2 `E2`,`S2`,`S3` →
  `rl-primary/releaselens-embed-small` (`text-embedding-3-small`), `E3` →
  `rl-primary/releaselens-embed-large` (`text-embedding-3-large`), `E1`,`S1` → `none/no-model`; P3
  `gpt` → `fwa/gpt-5.6-luna`, `claude` → `fwa/claude-haiku-4-5`. The implementer confirms P1's arm
  models from `arm_summaries[].models_seen` and P2's from the run file's `deployments` block.
  Account aliases are invented labels, never real resource names.
- `seeds/model_rates.csv` columns `model,input_per_m_usd,output_per_m_usd,rate_date,source`, rows:
  `gpt-5.6-luna,0.20,1.20,2026-10-04,P3 report.md`; `claude-haiku-4-5,1.00,5.00,2026-10-04,P3
  report.md`; `text-embedding-3-small,0.02,0,2026-10-02,P2 run file rates`;
  `text-embedding-3-large,0.13,0,2026-10-02,P2 run file rates`.

- [ ] **Step 1: Write the failing tests.**
  - `test_parse_p3_sums_tokens_and_scores`: a run with calls of (1168, 64) and (900, 30) gives
    `tokens_in=2068`, `tokens_out=94`; scores map to the three columns.
  - `test_first_attempt_is_superseded`.
  - `test_estimate_cost`: 1,000,000 in and 1,000,000 out on `claude-haiku-4-5` = 6.00.
  - `test_columns_and_utc`: the built DataFrame's columns equal `EVAL_COLUMNS`; a `+10:00` input
    timestamp is stored as the equal UTC instant.
  - `test_merge_rerun_changes_nothing`: merge the same DataFrame twice; the second `MERGE`'s
    `operationMetrics` show `numTargetRowsInserted == 0` and `numTargetRowsUpdated == 0`.
  - `test_merge_updates_changed_row`: change one `latency_ms`; exactly one row updated.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement** `eval_attempts.py` and write `seeds/dim_arm.csv` (spec below). P2
  arms `E1` and `S1` have cost 0 with `cost_basis="no_model"`; every arm's model for costing comes
  from `dim_arm.csv`'s `model` column.
- [ ] **Step 4: Run.** Expected: pass.
- [ ] **Step 5: Commit.** `git commit -m "Add silver eval attempts with MERGE"`

---

### Task 5: Silver costs and attribution

**Files:**
- Create: `src/lakehouse/silver/costs.py`, `src/lakehouse/attribution.py`, `seeds/rg_rules.csv`,
  `seeds/session_windows.csv`, `tests/test_costs.py`, `tests/test_attribution.py`,
  `tests/fixtures/cost_export_2026-10.csv` (synthetic, with the documented export header)

**Interfaces:**
- Produces: `COST_COLUMN_MAP: dict[str, str]` (export header → silver name) in one place, for:
  date, resource ID, resource group, meter ID, meter name, cost in billing currency, billing
  currency, tags. **Plan 2 confirms it against the first real export**; every other function uses
  silver names only.
- Produces: `parse_tags(raw: str | None) -> dict[str, str]` (JSON object, brace-less
  `"k": "v"` pairs, empty or `None` → `{}`; keys lower-cased).
- Produces: `@dataclass Attribution(project: str, rule: str)`;
  `attribute(resource_group: str | None, cost_date: date, tags: dict[str, str], rg_rules:
  list[RgRule], windows: list[SessionWindow]) -> Attribution`, applying spec 3.6 in order:
  1. `rg_rules` (exact name or `fnmatch` glob, case-insensitive, optional date window) → project;
  2. if the group is marked `shared` in `rg_rules`, the `session_windows` row covering
     `cost_date` → project, otherwise `releaselens-shared`;
  3. tag `project` (case-insensitive value): `releaselens` → `releaselens-shared`,
     `foundry-workshop-agent` → `p3`;
  4. else `untagged`.
  `rule` names which step matched (`rg_rule`, `shared_window`, `shared_default`, `tag`, `untagged`).
- Produces: `normalise_costs(spark, export_df, rg_rules, windows) -> DataFrame` (silver columns
  plus `billing_month` `YYYY-MM`, `project`, `attribution_rule`), and `replace_month(spark, df,
  table_path: str, billing_month: str) -> None` (Delta overwrite with `replaceWhere
  "billing_month = '<m>'"`; raises if `df` holds any other month).
- `seeds/rg_rules.csv` columns `pattern,project,shared,valid_from,valid_to,evidence`, rows:
  `rg-releaselens,p1,false,,,app stack (spec 3.6)`; `rg-releaselens-gateway,p4,...`;
  `rg-releaselens-functions,p5`; `rg-releaselens-webapp,p6`; `rg-p7-*,p7`; `rg-p8-*,p8`;
  `rg-fwa-*,p3`; `rg-releaselens-bootstrap,,true`; `rg-releaselens-search,,true`.
- `seeds/session_windows.csv` columns `project,start_date,end_date,evidence`, from the tracker log:
  `p1,2026-09-27,2026-10-02`; `p2,2026-10-03,2026-10-04`; `p4,2026-10-06,2026-10-09`;
  `p5,2026-10-10,2026-10-11`; `p6,2026-10-12,2026-10-14`; `p7,2026-10-15,2026-10-15`;
  `p8,2026-10-16,2026-10-21`. The implementer re-reads the tracker log to confirm each window and
  cites the log date in `evidence`; windows must not overlap.

- [ ] **Step 1: Write the failing tests.**
  - `test_parse_tags_formats` *(Review Focus 1)*: `'{"project":"ReleaseLens"}'`,
    `'"project": "ReleaseLens","purpose": "x"'`, `''` and `None` → `{"project": "ReleaseLens", ...}`
    or `{}`.
  - `test_rg_rule_beats_tag`: group `rg-releaselens-gateway` with tag `releaselens` → `p4`,
    `rule="rg_rule"`.
  - `test_shared_split_by_window`: `rg-releaselens-bootstrap` on 2026-10-03 → `p2`; on 2026-10-25 →
    `releaselens-shared`.
  - `test_tag_case_insensitive`: unknown group, tag `ReleaseLens` → `releaselens-shared`.
  - `test_untagged_fallback`: unknown group, no tag → `untagged`; group `None` → `untagged`.
  - `test_windows_do_not_overlap`: loads the real `seeds/session_windows.csv` and asserts no two
    windows overlap.
  - `test_replace_month_only_touches_that_month`: load September and October, replace October with
    a restated October; September unchanged.
  - `test_replace_month_shrinks` *(Review Focus 3)*: a restated October missing one resource → that
    resource's October rows are gone.
  - `test_no_row_dropped`: the sum of silver cost equals the fixture export's sum.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement** `attribution.py` (pure Python, loaded from the seeds) and `costs.py`
  (apply `attribute` through a UDF; data is small).
- [ ] **Step 4: Run.** Expected: pass.
- [ ] **Step 5: Commit.** `git commit -m "Add silver costs, month replace and attribution rules"`

---

### Task 6: Type 2 deployment dimension

**Files:**
- Create: `src/lakehouse/silver/deployments.py`, `seeds/deployment_changes.csv`,
  `tools/verify_deployment_seed.py`, `tests/test_deployments.py`

**Interfaces:**
- Consumes: `seeds/dim_arm.csv` (Task 4) for the `external` members.
- Produces: `DEPLOYMENT_KEY = ["account_alias", "deployment_name"]`; tracked attributes
  `model_name, model_version, sku, capacity, region_alias`.
- Produces: `scd2_rows(changes: DataFrame) -> DataFrame` and `apply_scd2(spark, changes: DataFrame,
  table_path: str) -> None`. Algorithm (not determined by the tests alone):
  1. effective time = `effective_from_override` if set, else `committed_at`, in UTC;
  2. per key, order by effective time and drop rows whose tracked attributes equal the previous
     row's (no-op changes);
  3. `valid_to` = next row's `valid_from` (`lead`), null for the last; `is_current = valid_to is
     null`; `deployment_sk` = first 16 hex chars of sha256 over key + `valid_from` ISO string;
  4. `MERGE` into the table on `deployment_sk`: update `valid_to`/`is_current` when they differ,
     insert when not matched. Re-applying the same changes is therefore a no-op.
- Members not from Terraform, added by `scd2_rows` as open rows from `1900-01-01`:
  `account_alias="external"` with `deployment_name` = the model (`claude-sonnet-5`,
  `gpt-4.1-mini@openai`, …, one per distinct non-Azure model in `dim_arm.csv`), and
  `account_alias="none", deployment_name="no-model"`.
- `seeds/deployment_changes.csv` columns
  `account_alias,deployment_name,model_name,model_version,sku,capacity,region_alias,committed_at,effective_from_override,source,repo,sha,path,evidence`.
  Rows (the implementer fills `committed_at` from `git log -1 --format=%cI <sha>` and the full
  SHAs; account and region aliases are invented labels, never real names):
  - `rl-primary/releaselens-chat`: gpt-4.1-mini 2025-04-14 GlobalStandard 100 at `c4dc300`; 300
    at `2b28318`.
  - `rl-primary/releaselens-embed-small` (text-embedding-3-small, 1, GlobalStandard, 350) and
    `rl-primary/releaselens-embed-large` (text-embedding-3-large, 1, 350) at `b3cc3fd`.
  - `rl-sea/releaselens-chat` (100), `rl-sea/releaselens-chat-failover-test` (100) and
    `rl-primary/releaselens-chat-failover-test` (1) at `f5c0f9c`.
  - `fwa/gpt-5.6-luna`: version `unpinned`, capacity 50 at `3c3b4e0`; 25 at `3c7ac46`; version
    `2026-07-09` at `4b73be9`.
  - `fwa/claude-haiku-4-5`: version 1, capacity 25 at `3c3b4e0`; version 2 at `92ee3cc`.
  - Manual (`source=manual`, no SHA): both `fwa` deployments at capacity 80, effective at the
    earliest `startedAt` of the P3 scored runs, evidence "tracker log 2026-10-06: raised to 80
    after the throttled dry run, before the study; value in git-ignored terraform.tfvars".
  Capacity values that come from a variable's default are read from that file's `variables.tf`
  at the same SHA.
- Produces: `tools/verify_deployment_seed.py --releaselens PATH --fwa PATH` (local only): for each
  git-sourced row, `git show <sha>:<path>` (and `variables.tf` at the same SHA) contains the row's
  model version and capacity; exits non-zero naming any mismatch.

- [ ] **Step 1: Write the failing tests.**
  - `test_new_version_closes_old`: two changes for one key → two rows; the first has `valid_to`
    equal to the second's `valid_from`, `is_current` false.
  - `test_reapply_is_noop`: `apply_scd2` twice with the same changes; second `MERGE` inserts 0 and
    updates 0.
  - `test_incremental_change`: apply 2 changes, then 3 → the old current row is closed and one row
    inserted.
  - `test_noop_change_collapses`: a change repeating the same attributes adds no row.
  - `test_two_changes_same_day`: ordered by time, both kept.
  - `test_override_wins`: `effective_from_override` replaces `committed_at`.
  - `test_external_and_none_members`: present, open, `valid_from = 1900-01-01`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement** `deployments.py`, write both seeds, write the verify tool.
- [ ] **Step 4: Run** `pytest tests/test_deployments.py -q` and
  `python tools/verify_deployment_seed.py --releaselens ../ReleaseLens --fwa ../foundry-workshop-agent`.
  Expected: tests pass; the tool reports every git-sourced row verified.
- [ ] **Step 5: Commit.** `git commit -m "Add Type 2 deployment dimension and its seeds"`

---

### Task 7: Gold star schema

**Files:**
- Create: `src/lakehouse/gold/__init__.py`, `src/lakehouse/gold/build.py`,
  `src/lakehouse/gold/fingerprint.py`, `seeds/live_checks.csv`, `tests/test_gold.py`

**Interfaces:**
- Consumes: silver tables from Tasks 4–6; `seeds/dim_arm.csv`, `seeds/live_checks.csv`.
- Produces: `build_gold(spark, paths: LakePaths, seeds_dir: str) -> dict[str, int]` (table → row
  count). Full overwrite of every gold table (data is tiny; overwrite is the simplest idempotent
  choice). Tables: `dim_date` (every day from 2026-09-01 to the latest fact date), `dim_project`
  (`p1`…`p8`, `releaselens-shared`, `untagged`), `dim_study`, `dim_arm`, `dim_model_deployment`
  (copy of silver), `fact_eval_attempt`, `fact_daily_cost`, `fact_live_check`, and the reject
  table `reject.eval_unresolved`.
- The Type 2 join: attempt → `dim_arm` on (study, arm) → `dim_model_deployment` on
  (account_alias, deployment_name) and `valid_from <= started_at < coalesce(valid_to, 9999-12-31)`.
  Zero or several matches → the row goes to `reject.eval_unresolved` with `reason` (`no_version`
  or `multiple_versions`) and is not in the fact.
- Each gold table's grain is a module-level constant (`GRAINS: dict[str, str]`) copied from spec
  3.3; plan 2 renders it into the README.
- Produces: `gold_fingerprint(spark, paths: LakePaths) -> dict[str, str]` in
  `src/lakehouse/gold/fingerprint.py` (per gold and reject table: sha256 over rows sorted by all
  columns, serialised as JSON lines). Task 8 consumes it.
- `seeds/live_checks.csv` columns
  `project,session_date,check_id,verdict,measured_value,unit,source_report,source_sha`, verdict
  in `pass|fail|not-run`. Transcribed from `docs/gateway-report.md`, `functions-report.md`,
  `webapp-report.md`, `agents-report.md` (+ `agents-results-2026-10-15.json`) and
  `pipelines-report.md` at ReleaseLens `origin/main`, keeping every failure and not-run row.

- [ ] **Step 1: Write the failing tests.**
  - `test_boundary_joins_new_version`: `started_at == valid_from` of version 2 → version 2.
  - `test_mixed_offsets` *(Review Focus 2)*: a `+10:00` commit time and a `+00:00` result time
    for the same instant join as equal.
  - `test_external_and_no_model_arms_resolve`.
  - `test_before_first_version_rejected`: lands in `reject.eval_unresolved` with
    `reason="no_version"`, absent from the fact, not lost (fact + reject = input rows).
  - `test_rebuild_is_identical`: `build_gold` twice → identical row counts and identical
    `gold_fingerprint`.
  - `test_fingerprint_detects_change`: altering one gold value changes that table's fingerprint
    only.
  - `test_live_checks_seed_valid`: every row's verdict is in the allowed set and every
    `source_report` file exists in the manifest or is named in spec 4.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement** `build.py` and transcribe `live_checks.csv`.
- [ ] **Step 4: Run.** Expected: pass.
- [ ] **Step 5: Commit.** `git commit -m "Add gold star schema with Type 2 join and reject table"`

---

### Task 8: Quality checks, answers and the `run_load` entry point

**Files:**
- Create: `src/lakehouse/checks.py`, `src/lakehouse/answers.py`, `src/lakehouse/run.py`,
  `seeds/published_figures.csv`, `tests/test_checks.py`, `tests/test_run.py`,
  `tests/fixtures/lake/` (a complete synthetic bronze + private tree for one load date)

**Interfaces:**
- Produces: `@dataclass CheckResult(name: str, passed: bool, detail: str)`;
  `class QualityCheckFailed(Exception)` carrying the failed results.
- Produces the four checks of spec 3.7:
  - `check_deployment_resolution(spark, paths) -> CheckResult` (fails if
    `reject.eval_unresolved` has any row);
  - `check_cost_totals(spark, paths, export_df) -> CheckResult` (per month, silver total vs export
    total, tolerance 0.01);
  - `check_published_figures(spark, paths, figures_csv: str) -> CheckResult`;
  - `check_rerun_unchanged(before: dict[str, str], after: dict[str, str]) -> CheckResult`,
    comparing two `gold_fingerprint` results (Task 7).
- `seeds/published_figures.csv` columns `figure_id,study,query,expected,tolerance,source`:
  - `p1_total_cost_usd`: sum of `cost_usd` over P1 scored rows (which already includes the judge
    cost, Task 4) = `2.7055`, tolerance `0.0001`, source `eval/baseline.md`;
  - `p3_gpt_success`: `59` (of 60 scored) and `p3_claude_success`: `57`, source P3 `report.md`;
  - `p2_n_per_arm`: `229` scored rows for each of the six arms;
  - `p2_e3_minus_e1_top1`: `0.271`, tolerance `0.0005`, source ReleaseLens README — only if Task
    2 Step 5 confirmed artefact-match top-1 is the published metric; otherwise omit the row and
    record why in the commit message.
  `query` names one of a fixed set of query functions in `checks.py`; no free-form SQL in the CSV.
- Produces `answers.py`: `cost_per_project(spark, paths) -> DataFrame` (project, billing
  currency, total, with `untagged` and `releaselens-shared` rows always present, zero if empty);
  `quality_across_models(spark, paths) -> DataFrame` (study, arm, model, model_version, n, metric
  means, scored rows only); `cost_per_result(spark, paths) -> DataFrame` (project, results,
  estimated USD per result, actual per result in billing currency, side by side).
- Produces: `@dataclass RunSummary(load_date: date, row_counts: dict[str, int], checks:
  list[CheckResult], fingerprint: dict[str, str])` and `run_load(spark, paths: LakePaths,
  seeds_dir: str, private_root: str, cost_export_path: str, load_date: date) -> RunSummary`:
  bronze ingest is **not** called here (plan 2's pipeline runs it as its own activity); this
  reads bronze for `load_date`, builds silver and gold, runs the checks, and raises
  `QualityCheckFailed` if any fails. It raises `FileNotFoundError` naming the path if the cost
  export for the month is missing or empty, before writing anything.

- [ ] **Step 1: Write the failing tests.**
  - `test_each_check_passes_on_good_data` and one `test_<check>_fails` per check, each breaking
    one fixture: a result timestamp before every version; one cost row removed from silver; P3
    success changed to 58; a gold row altered between fingerprints.
  - `test_run_load_twice_is_identical`: two `run_load` calls on the fixture tree for the same date
    → equal fingerprints and equal row counts.
  - `test_run_load_raises_on_failed_check`: a broken fixture raises `QualityCheckFailed` whose
    message names the check.
  - `test_missing_cost_export_fails_before_writing` *(Review Focus 5)*: empty export → raises
    `FileNotFoundError` naming the path; gold table versions unchanged.
  - `test_answers_include_untagged`: `cost_per_project` always has an `untagged` row.
  - `test_cost_per_result_never_mixes_currencies`: estimated and actual are separate columns,
    each with its own currency column.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement** `checks.py`, `answers.py`, `run.py`, and the seed.
- [ ] **Step 4: Run the whole suite** `pytest -q` and `python tools/check_hygiene.py`.
  Expected: all pass, no hygiene problems.
- [ ] **Step 5: Local dress rehearsal on real data (local only, nothing committed).** Run
  `run_load` against a local root with the real P3 files (fetched via Task 3 into a scratch
  root), the real `private/` extracts, and a synthetic cost export. Expected: the published
  figures check passes on real data; the resolution check passes (if it fails, the reject table
  names which results need an `effective_from_override`, with evidence, in
  `deployment_changes.csv`); the second run's fingerprint equals the first.
- [ ] **Step 6: Commit.** `git commit -m "Add quality checks, gold answers and run_load entry point"`

---

## After plan 1

- Update the tracker log in `portfolio-plan` (approved push): plan 1 done, test counts, any
  deviation.
- Then write plan 2 (live). Nothing in plan 1 touches Azure, Fabric or GitHub.
