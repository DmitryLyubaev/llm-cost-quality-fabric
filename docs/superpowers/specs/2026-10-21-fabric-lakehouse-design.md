# Project 9: LLM cost and quality on a Fabric lakehouse — design

Written 21 October 2026 · Phase 4, project 9 of 11 · Status: draft for owner approval

Brief: `portfolio-plan/handovers/project-09-fabric-lakehouse.md` at `ef9aa84`. Where this spec and
the brief differ, this spec records the owner's later decision and says so.

## 1. Intent

A CV piece. It shows that the owner has built on Microsoft Fabric with their own hands, for
senior, principal and AI engineer roles.

- **The subject is AI in production:** what the portfolio's LLM systems cost, and how their
  quality compared across models. Fabric is the skill being shown; LLM evaluation and cost are
  what it is about.
- **The senior signal is judgement:** decisions with the rejected options written down,
  simplicity under constraints, cost measured, and failures and gaps kept in the record.
- **The data is small** (about 1,800 result rows and a few hundred cost rows). The README says so
  plainly: small data, real platform, every number measured and dated.

### Goals

1. A public repo, `github.com/DmitryLyubaev/llm-cost-quality-fabric`, with a README in
   ReleaseLens's style.
2. Bronze, silver and gold in a Fabric lakehouse, loaded by a date-parameterised Fabric pipeline.
   Running the same date twice changes nothing.
3. Three dated answers from gold, each published even if the answer is "the data can't tell":
   - cost per project, with untagged spend shown
   - quality across models (and across versions, where the data has any)
   - cost per measured result
4. CI/CD from day one: dev connected to git, prod deployed by a pipeline as a service principal.
5. The trial capacity's actual use, measured.

### Non-goals

- Showing every Fabric feature. Warehouse, Dataflow Gen2, mirroring and Fabric's built-in
  deployment pipelines stay "know, don't build", as phase 4 says.
- New paid evaluation runs. The project loads what projects 1–8 left; it re-runs no study.
- Streaming (project 10), the semantic model, DAX, row-level security and the report (project 11).

### Anti-goals: a failure even if everything runs

- Any claim the data doesn't support, including "quality improved across versions".
- The work account, or anything from the employer, appearing anywhere.
- Raw private data, answer text, tenant or subscription IDs, storage account names or endpoint
  URLs in the public repo, a notebook output or a public log.
- Unattributable spend quietly dropped.

## 2. Constraints

From `RULES.md` and the brief, unchanged:

- **Identity.** Fabric signs in as a work or school user in the personal subscription's own
  `…onmicrosoft.com` tenant; never the work account, never a personal Microsoft account. Before
  any Azure command, `az account show`; stop if the signed-in user is the work account. If
  Fabric sign-up fails with the personal-tenant user, stop and tell the owner.
- **Commits** as `Dmitry Lyubaev <Dmitry.Lyubaev@gmail.com>` (already set in this clone). Check
  `git log --format='%an <%ae>'` before every push.
- **No secrets** beyond Fabric's GitHub connection PAT: fine-grained, this repo only, with an
  expiry. Everything else keyless: OIDC, workload identity federation, workspace identity.
- **Protect the bill.** The A$10 budget alert already exists. Deploy workflows run only from
  `main`; the federated credential is restricted to a protected environment. No F SKU without
  the owner.
- **Norton inspects TLS** on this PC. `az` takes a CA bundle per command; expect pip, Python and
  the Fabric CLI to need the same. Read status codes before bodies.
- **Ask the owner before:** creating or publishing the GitHub repo, any push, starting the Fabric
  trial, creating workspaces, the service principal or federated credentials, creating the cost
  export, and anything that bills. Pushing tracker updates to `portfolio-plan` is already approved.
- **Trial timing (owner decision, 21 Oct).** The 60-day trial starts on the first day of
  implementation, not the day brainstorming started, so it covers building projects 9–11.

## 3. Decisions

### 3.1 Repo name: `llm-cost-quality-fabric`

Names the AI subject and the platform, which is what a CV reader scans for.

- *Rejected: `portfolio-telemetry-fabric`* (the brief's suggestion). "Portfolio" is about the
  author, not the work, and "telemetry" suggests streaming, which is project 10.

The local folder is `E:\Projects\Fabric`; renaming it to match the repo is the builder's call.

### 3.2 Prod deploy: GitHub Actions with OIDC

- The repo, its pytest CI and the PR flow already live on GitHub; `fabric-cicd` is a Python tool
  and runs the same in either CI.
- A `prod` GitHub environment, `main` only, with the owner as required reviewer. The service
  principal's federated credential is bound to that environment.
- *Rejected: Azure Pipelines* (the phase 4 plan). Project 8 already shows it, and every Azure
  DevOps identity was destroyed; setting them up again proves nothing new and splits CI across
  two systems.

### 3.3 The gold model

| Table | Grain (one row per…) | Source | Notes |
|---|---|---|---|
| `fact_eval_attempt` | study × item × arm × pass × repeat | P1, P2, P3 results | ~1,760 rows. Measures: success, tokens in/out, latency, estimated cost (USD), and each quality metric, null where the study didn't measure it |
| `fact_daily_cost` | cost-export line: resource × meter × day | Cost Management export | Actual cost in the billing currency; project from attribution rules (3.6) |
| `fact_live_check` | check × session | P4–P8 public reports | ~50 rows; verdict, measured value, unit, date, source report and commit SHA |
| `dim_model_deployment` | deployment version (Type 2) | Terraform git history | `valid_from`, `valid_to`, `is_current`; model, version, SKU, capacity, region, account |
| `dim_project` | project 1–8, plus `releaselens-shared` and `untagged` | seed | |
| `dim_study` | study run | results files | P1 three-arm, P2 six-arm, P3 agent study |
| `dim_arm` | arm within a study | results files | maps arm → deployment name |
| `dim_date` | day | generated | |

Each grain is written in the repo next to its table's code, as the brief requires.

- **Why three facts.** Live checks are pass/fail checks per session, not item × arm × pass
  results; folding them into `fact_eval_attempt` would break its grain. Costs are per resource
  per day, a third grain.
- **Why one wide results fact.** Each study measured different metrics. Nullable metric columns
  on one fact keep it simple; quality is only ever compared within a study.
  *Rejected: a narrow metric-per-row fact.* More correct in the abstract, more joins for three
  studies.
- **The second gold answer is "quality across models".** The inventory shows no model changed
  version between two measured runs: `gpt-4.1-mini` stayed `2025-04-14` throughout (its changes
  were capacity), and Foundry's version changes all landed on 5 Oct, before the 6 Oct study. So
  the comparisons are GPT vs Claude (P3), small vs large embeddings (P2), and Azure OpenAI vs
  OpenAI on one model (P1). "Across versions: the data can't tell" is published as such. The
  Type 2 dimension is still built, still joins every result, and still records the capacity
  changes.
- **Type 2 effective dates** are the commit time on `main` of each change, a proxy for apply time
  (git doesn't record applies). The README states this. Where a result was measured after an
  apply but before its commit reached `main`, a committed override seed sets that version's
  `valid_from`, citing the tracker log entry as evidence. Overrides are listed in the README.
- **One version isn't in git at all:** Foundry's capacity raise from 25 to 80 exists only in a
  git-ignored `terraform.tfvars` and README prose. It enters as a hand-entered seed row, flagged
  `source = manual`. The tracker log for 6 Oct says the dry run was throttled at 25 and both
  capacities were raised to 80 before the study, so its `valid_from` is the earliest `startedAt`
  of the P3 study's scored runs. *(My call.)*
- **Not every arm has an Azure deployment.** P1's OpenAI arm calls OpenAI directly, and some P2
  arms (keyword search) use no model. These resolve to explicit members of
  `dim_model_deployment`: an `external` row per non-Azure model, taken from the study's own
  record of model and version, and one `no model` row. So every result row resolves to exactly
  one member, and a row that resolves to none is a genuine failure. *(My call.)*
- **The dimension is maintained with Delta `MERGE`**, the textbook pattern: a changed version
  closes the open row and inserts a new one; an unchanged re-run is a no-op.

### 3.4 How files reach bronze

| Source | Route | Why |
|---|---|---|
| Public repo files (P3 results, public reports, `questions.jsonl`, Terraform files at each change commit) | A notebook activity in the pipeline fetches them from GitHub, pinned to a commit SHA held in a manifest in the repo | A re-run is reproducible; a new result is a manifest change, not a manual upload. A notebook needs no per-workspace HTTP connection to parameterise |
| Private extracts (P1 and P2 per-query scores) | A local script in the repo (`tools/extract_private.py`) writes sanitised JSONL to a git-ignored folder; the owner uploads it once to `Files/bronze/private/` | The pipeline cannot reach private local files. The script is public, its output is not |
| Cost exports | Cost Management export → a small storage account → OneLake shortcut, read through the workspace identity | Keyless; bronze needs no copy |
| P4–P8 live checks | A CSV transcribed from the public reports, committed to the repo, with the source report and commit SHA on every row | All of it is already public; parsing markdown tables in Spark would be fragile |

Every bronze landing is as-is, under a `load_date=` folder.

- *Rejected: everything as a one-off upload behind a shortcut.* A re-run wouldn't pick up new
  results, and it hides the pipeline skill.
- *Rejected: everything through the pipeline.* The private files can't be reached that way.

The brief's "Decided 2" said public files are copied in a pipeline step and its "Open 4" asked the
question again; this resolves both.

**Private data (owner decision, 21 Oct).** Private per-query scores may load into the Fabric
workspace, which is not public, provided answer text and identifiers are removed first. Raw
private files never enter the repo. Excluded entirely: P8 teardown files and Terraform state, the
P2 question-writing side data, and P4–P6 raw request files (the public reports carry what the
gold model needs).

### 3.5 Cost history window

- **From 1 September 2026 to the present,** so the first portfolio spend (late September) is
  inside it with margin.
- **Free Trial is a supported offer** in Cost Management ([supported offers][offers]), and the
  subscription is the same one before and after the 30 September upgrade, so one subscription-scope
  export should cover both. Exports offer a one-time export plus a daily month-to-date
  export ([exports][exports]).
- **To verify before building, read-only:** that a one-time export reaches back to September for
  this subscription, and whether `ActualCost` exports work for it (a community report says some
  pay-as-you-go subscriptions get only `AmortizedCost` and usage). If September can't be
  exported, the window starts where the data starts and the README says so.
- **Silver replaces the whole month** on each daily export, because exports restate the month to
  date.
- **Currency.** Costs stay in the export's billing currency. Repo cost figures are USD list-price
  estimates; they are never converted with an invented rate. If the export carries an exchange
  rate, use it and say so.

[offers]: https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/understand-cost-mgt-data
[exports]: https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/tutorial-improved-exports

### 3.6 Cost attribution

The `project` tag names a repo ("releaselens" covers projects 1, 2 and 4–8), so it can't win over
a more specific rule. Applied in order; the first match wins: *(my call)*

1. A committed seed of resource-group rules: name pattern, optional date window, project, and the
   evidence for the rule. For example, `rg-fwa-*` → P3, `rg-p7-*` → P7, `rg-p8-*` → P8,
   `rg-releaselens-gateway` → P4.
2. **Shared resources split by day.** The shared search group and the bootstrap Azure OpenAI
   accounts are split by date window, using the tracker's session dates (for example P1 on 2 Oct,
   P2 on 3–4 Oct, P5 on 10–11 Oct, P8 on 18–21 Oct). Without this, P1's and P2's model spend
   would sit in a shared bucket and "actual cost per result" would mean nothing for them.
3. A `project` tag, compared case-insensitively, mapped to `releaselens-shared` or
   `foundry-workshop-agent` → P3.
4. Shared bootstrap spend on days with no session (storage, Log Analytics) goes to
   `releaselens-shared`, not split across projects.
5. Everything else goes to `untagged`, which is a reported bucket, never dropped.

Spend outside Azure (P1's OpenAI arm, Anthropic credit for P2's question generation) is not in
Cost Management. The actual figure for those projects covers Azure only, and the README says so.

### 3.7 Quality checks: few, each guarding a claim

| Check | Guards | Fails the pipeline when |
|---|---|---|
| Every result row resolves to exactly one `dim_model_deployment` member (including `external` and `no model`) | The Type 2 join behind "quality across models" | Any row resolves to zero or more than one member; the unresolved rows are written to a reject table, never dropped |
| Each month's silver cost total equals the export's total | "Untagged spend stays visible; nothing is dropped" | Totals differ by more than 0.01 in the billing currency |
| Gold reproduces published figures: P1 study cost $2.7055, P3 success 59/60 (GPT) and 57/60 (Claude), P2 n = 229 per arm | That gold agrees with what the READMEs already claim | Any figure differs |
| Re-running a load date leaves every gold table unchanged | "Running the same date twice changes nothing" | Row counts or content hashes change |

Each check is shown failing once on purpose, and that failed run is kept in the record.

### 3.8 Cost per measured result

Two figures, side by side, never blended:

- **Estimated:** the USD cost each study recorded or implies. P1 has `cost_usd` per outcome. P2 is
  tokens × the per-1,000 rates in its run file. P3 is tokens × the price table in its report.
- **Actual:** the project's attributed Cost Management total, in the billing currency, divided by
  its result count.

The gap between the two is a finding in itself.

## 4. Source inventory

Taken 21 October 2026, read-only; local `main` matched `origin/main` in both repos. The builder
re-checks before relying on it.

| Source | Status | Count and grain | Use |
|---|---|---|---|
| P1 `eval/baseline.md`, README | Public | Summary per arm | Reconciliation figures |
| P1 per-outcome results (`eval/reports/2c413fe9-….json`) | Private, holds answer text | 90 = query × arm × pass; model with version, metrics, tokens, `cost_usd` | Sanitised extract → `fact_eval_attempt` |
| P1 August and dry runs | Private | 3–15 outcomes each; August lacks model | Excluded: before the deployment existed |
| P2 `eval/retrieval/questions.jsonl` | Public | 229 questions | `dim` attributes for items |
| P2 six-arm results (`retrieval-20261003T210637Z.json`) | Private, contains identifiers | 1,374 = 229 × 6, plus 180 repeats; model only at run level | Sanitised extract → `fact_eval_attempt` |
| P3 `results/study-2026-10-06-2601d16c644b/` | Public | 120 runs = scenario × engine × pass, plus 9 repeats; no cost field | Pipeline fetch → `fact_eval_attempt` |
| P4–P8 reports | Public | ~50 checks | Transcribed CSV → `fact_live_check` |
| P7 `agents-results-2026-10-15.json` | Public | 6 checks, 3 runs | `fact_live_check` |
| P7 private originals | **Lost** | — | Stated in README |
| P4/P5 raw, P6 raw (in Codex worktrees), P8 evidence | Private | — | Excluded (see 3.4) |
| Terraform history, both repos | Public | 4 ReleaseLens and 4 Foundry change commits | `dim_model_deployment` |
| Foundry 25 → 80 capacity raise | Not in git | 1 change | Manual seed row |
| Cost Management | Not exported yet | — | `fact_daily_cost` |

**Still to check, read-only, before the cost window is fixed:** whether the ReleaseLens and
Foundry bootstrap stacks still exist (storage, Log Analytics, Application Insights, six model
deployments). They don't bill by the hour, but they appear in the cost data.

## 5. Tests

**Unit tests (pytest, local Spark, in CI, on synthetic fixtures only — never private data):**

- each silver transform: types, deduplication, and the `MERGE` keys
- attribution: tag case, resource-group pattern with date window, shared bucket, the untagged
  fallback
- the cost month replace: a restated month replaces only that month
- Type 2 `MERGE`: a new version closes the old row; an unchanged re-run is a no-op; two changes on
  one day
- the Type 2 join: a result exactly at a boundary; an `external` and a `no model` arm; a result
  before the first version (lands in the reject table and fails the check, never dropped)
- the private extractor: output has no answer text and nothing matching a GUID, storage-account or
  endpoint pattern

**In Fabric:**

- the pipeline run twice for one date, with gold content hashes compared
- each quality check in 3.7 failing once on purpose, with the failed run kept
- a backfill as a single run

## 6. Environments and deployment

- **Two workspaces, dev and prod,** both on the trial capacity. *(My call on the branching.)*
  - Dev is connected to a `dev` branch through Fabric's git integration, because Fabric commits
    straight to its bound branch and can't open pull requests.
  - `main` is protected: changes arrive only by pull request from `dev` (or a feature branch),
    with pytest CI passing.
  - A merge to `main` triggers `fabric-cicd` from GitHub Actions, as the service principal,
    behind the `prod` environment's approval.
- **Both workspaces load the real data.** It is tiny, so there's no need for a fake dev dataset.
  Each workspace identity gets read access to the cost-export storage, and the private extract is
  uploaded to both. *(My call.)*
  - **Prod is the source of record:** the published answers and the capacity measurement come
    from prod.
  - **Dev is where the quality checks are broken on purpose.** That's the same code, without
    putting deliberate failures in prod's history.
- **Terraform's `microsoft/fabric` provider** creates the workspaces and role assignments. It is
  run by the owner from this PC, with local, git-ignored state. CI doesn't run Terraform.
- **Identifiers in git (owner decision, 21 Oct).**
  - **May be committed:** Fabric's own workspace and item IDs, as Fabric's git sync writes them.
    They aren't secrets and are useless without access to the tenant.
  - **Never committed:** tenant and subscription IDs, storage account names, and endpoint URLs.
    These live in GitHub environment secrets.
  - **The cost-export shortcut** is created by a setup step (a notebook or the Fabric API, reading
    the storage address from a parameter). It is not synced to git, so its target never lands in
    the repo.
- **Parameters.** Item IDs, workspace IDs and connections differ per workspace and go in
  `fabric-cicd`'s parameter file.
- **Tenant setting:** service principals may call Fabric's APIs. Turned on by the owner in the
  personal tenant; the README notes that in an enterprise this is a request to the admin.
- **Git hygiene:** notebooks are committed without outputs.
  - **A CI check rejects notebook outputs**, and any occurrence of the actual tenant ID,
    subscription ID or storage account name, which it reads from secrets.
  - **It does not match on GUID patterns,** which would block every Fabric commit.
  - **The private extractor's own test** still rejects any GUID in its output, because private
    results should contain none.

## 7. Capacity

The trial is F4 or F64 ([trial][trial]). Record which, then measure in the Capacity Metrics app:
capacity units used per item for one full load and one backfill. The README reports what was
measured and the smallest paid SKU that fits, without buying one.

[trial]: https://learn.microsoft.com/en-us/fabric/fundamentals/fabric-trial

## 8. Scope cut

**In project 9:** everything in sections 3–7, and the three gold answers published as dated query
results in the README.

**Waits for project 10:** gateway events, Event Hubs, the Eventhouse, KQL, and the Real-Time
dashboard.

**Waits for project 11:** the Direct Lake semantic model, DAX measures, row-level security, the
report, and checking Power BI licensing on the trial.

**Not in this phase:** Warehouse, Dataflow Gen2, mirroring, Fabric deployment pipelines, the data
agent, any paid F SKU.

**If time runs short** (no deadline was set beyond the 1½-week estimate), cut in this order:
`fact_live_check` first, then the P2 private extract (P1 and P3 still answer the question). The
cost fact, the Type 2 dimension and CI/CD are never cut; they are the skill being shown.

## 9. Left to the builder

- Notebook and pipeline structure, table and column names beyond those above.
- How Terraform history is parsed (for example, the HCL at each change commit).
- The storage account's details for the cost export, within "keyless, cheapest, own small
  Terraform".
- The README's layout, within ReleaseLens's house style.

## 10. Risks

- **Fabric sign-up may fail for the personal-tenant user.** Stop and tell the owner; never fall
  back to the work account.
- **September cost data may not export.** Shrink the window and say so.
- **Fabric changes monthly.** The brief's Fabric details come from knowledge to mid-2026; check
  each against current docs as it's used.
- **Commit time is a proxy for apply time** in the Type 2 dimension. Stated in the README.
