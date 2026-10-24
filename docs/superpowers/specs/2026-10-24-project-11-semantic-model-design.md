# Project 11: semantic model, report and security — design

Written 24 October 2026 · Phase 4, project 11 of 11 · Status: draft for owner approval

Phase plan: `portfolio-plan/plan/phase-4.md` section 11. Builds on project 9's gold tables
(`docs/superpowers/specs/2026-10-21-fabric-lakehouse-design.md`) and project 10's gateway session
(`docs/superpowers/specs/2026-10-23-project-10-realtime-gateway-design.md`), both in this repo.
Recon behind this design, 24 October 2026: Microsoft Learn on Direct Lake, Fabric git, fabric-cicd,
Power BI licensing, RLS testing and semantic link, cited in
`docs/research/2026-10-24-semantic-model-facts.md`.

## 1. Intent

A CV piece for senior and AI-engineer roles. It shows the Power BI side of Fabric done properly:
a Direct Lake semantic model with dynamic row-level security, deployed through the same pipeline
as projects 9 and 10, whose numbers are **checked against gold** rather than trusted. A reviewer
sees the model and report as code in git, dated screenshots, and a repeatable check that the
report says what the lakehouse says and that each role sees only what it should.

Nothing is shared publicly. A public "publish to web" link would break the no-identifiers rule and
need a tenant setting turned on. *(Owner, 24 Oct.)*

### Goals

- A Direct Lake on OneLake semantic model over `lh_portfolio`'s gold tables, in git as TMDL,
  deployed to dev by git sync and to prod by `fabric-cicd`.
- **Dynamic row-level security by project** (cost showback): a project lead sees only their own
  project's rows. That includes shared spend the session-window rule has already allocated to
  their project. Untagged and unallocated shared spend stay with the owner. *(Owner, 24 Oct, on
  Claude's proposal.)*
- DAX measures that mirror the existing gold answers, so the report invents no figure.
- A report with three pages: spend by project, quality across models, and gateway health
  (wireframe agreed 24 Oct).
- **A model-versus-gold check:** a notebook evaluates every measure and compares it with gold's
  own answer functions. It also runs as each test user and confirms they see only their project.
  *(Claude's proposal, agreed 24 Oct.)*
- Dated screenshots of the three pages and of the security checks, with identifiers checked.

### Non-goals

- A public or embedded report, Power BI apps, or sharing beyond the two test users.
- Import-mode or DirectQuery models; a Direct Lake on SQL endpoint model. Its fallback to
  DirectQuery is covered as a "know, don't build" note in the README.
- New data. Every figure comes from projects 9 and 10's gold tables.
- The optional capstone (a Fabric data agent).

### Anti-goals: a failure even if it works

- **A report figure that differs from gold** and nothing fails.
- **A role that can read past its filter**, for example through direct OneLake access to the
  gold tables.
- **"Quality by model version" shown as if versions changed.** None did between measured runs:
  the page compares models and states the version.
- **An identifier** in the repo, a screenshot or the README: a UPN, tenant or subscription ID,
  Entra object ID, or URL. The access table holds UPNs, so it is never committed. Fabric workspace
  and item IDs may be committed (project 9's owner decision, 21 Oct), so the model's OneLake
  expression in git holds dev's real IDs, which git sync needs.

## 2. Constraints

- Projects 9 and 10's constraints hold unchanged:
  - identity rules: the `fabric@` user in the personal tenant, never the work account;
  - no identifiers in public files;
  - commits as `Dmitry Lyubaev <Dmitry.Lyubaev@gmail.com>`;
  - the owner's yes before every outward step;
  - Terraform runs in WSL.
- **Order:** after project 9's live run (gold exists in prod) and project 10's session (gateway
  data exists). The offline build can happen now.
- **Licence deadline.** Creating or editing the model and report needs a Power BI licence even
  on the trial capacity.
  - The `fabric@` user's Power BI individual trial started about 22 October, so it likely lapses
    about 21 December. The Fabric trial runs to about 7 January.
  - Live authoring finishes by about 20 December. Otherwise the fallback is one month of Power BI
    Pro, AU$21 a user a month plus GST.
  - Check the licence state in Account manager right after the Fabric trial starts.
- **Test users.** The owner creates two member users in Entra, `lead-p4` and `lead-p3`, with no
  licence. Claude never creates accounts. Impersonation and "Test as role" need real users;
  made-up UPNs and service principals can't be impersonated. *(Owner, 24 Oct.)*
- **Preview friction.** Git sync for semantic models and reports is still in preview. The `dev`
  branch must allow direct commits; project 9's ruleset protects only `main`.

## 3. Approaches

| | What | For | Against |
|---|---|---|---|
| **A (chosen)** | Direct Lake on **OneLake**, model RLS, TMDL in git, report authored in dev's web editor and committed as PBIR | Microsoft's current direction; RLS never causes a fallback; the model is text, so it can be built and tested offline now | Newer; errors instead of falling back; needs a refresh after deploy and a file-count discipline |
| B | Direct Lake on the **SQL analytics endpoint** | Better documented; can demonstrate fallback to DirectQuery on purpose | Depends on the endpoint's metadata sync; RLS on the endpoint forces the fallback |
| C | **Import-mode** model from Power BI Desktop, saved as a project (`.pbip`) | Most portable; Desktop is free | Not Direct Lake, which the phase plan asks for; shows Fabric less; Desktop live edit needs a licence anyway |

The owner chose **A** on 24 October 2026. B's fallback behaviour goes in the README as a "know,
don't build" note.

## 4. Design (decided)

### 4.1 The model `sm_portfolio`

- **Storage mode:** Direct Lake on OneLake, over `lh_portfolio`'s `gold` schema. Tables are named
  by the bare table name with `schemaName: gold`. The TMDL expression points at OneLake by
  workspace and lakehouse ID.
- **Tables:**
  - facts: `fact_daily_cost`, `fact_eval_attempt`, `fact_live_check`, `fact_gateway_call`;
  - dimensions: `dim_project`, `dim_date`, `dim_study`, `dim_arm`, `dim_model_deployment`;
  - one security table, `user_project` (4.2).
- **Relationships:**
  - single-direction from dimensions to facts;
  - `dim_project` filters every fact through `project`;
  - `dim_date` filters through each fact's date;
  - `dim_model_deployment` filters `fact_eval_attempt` through the resolved deployment version;
  - `user_project` has no relationship: the role filters each table with a `project` column
    itself (4.2).

  No bidirectional filters: the RLS pattern needs none, because no filter travels from
  `user_project`.
- **Gold gains keys and project columns** *(Claude's call)*, because the builder check found
  RLS could not reach the quality data:
  - `project` on `dim_study` and `fact_eval_attempt`, from the study-to-project map that now lives
    only in `answers.py` (`STUDY_PROJECT`). The map moves into a seed with evidence, like the
    other mappings;
  - single-column keys where Direct Lake needs them: `study_key` on `dim_study`, `arm_key` on
    `dim_arm` (study and arm), and the deployment version's key on `fact_eval_attempt`. They are
    built in gold, not as model calculated columns, which are in preview on OneLake.

  These are derived columns, not new data. Project 9's fingerprint changes once, by design, and
  its tests are updated.
- **Session tables always exist** *(Claude's call)*. A Direct Lake on OneLake model errors on a
  missing table. So the gold build creates an empty `fact_gateway_call` (and `security.user_project`)
  with their schemas when absent, and never overwrites a filled one. The model therefore loads
  before project 10's session has run, and in dev, where the gateway table stays empty.
- **Measures:** on their fact tables, in a display folder per page: Spend on `fact_daily_cost`,
  Quality on `fact_eval_attempt`, Gateway on `fact_gateway_call`. There is no measures-only table,
  because that would be a calculated table, which the model doesn't use *(Claude's call, ruling R1)*.
  Each measure names the gold answer it mirrors in its description.
  - **Spend:** `Cost (AUD)` and `Untagged cost`. They mirror `answers.cost_per_project`. Costs
    stay in the billing currency, and estimates are never added to actuals. There is no share
    measure: under RLS a lead's "share" would be 100% of what they can see, and gold has no share
    figure to mirror. The owner's table may show a visual-level percent of the grand total, which
    is outside the check.
  - **Quality:** `Results (n)`, `Success rate`, and one mean measure per metric that
    `quality_across_models` returns, named after it: groundedness, citation recall and
    precision, must-contain rate, top-1 rate, MRR, tool-call accuracy, task adherence, intent
    resolution and latency. A measure can't share a name with a column, and DAX names ignore case,
    so the groundedness mean is `Groundedness (mean)` *(ruling R4)*. There is no blended "score":
    each study shows the metrics it has.
    Also `Estimated cost per result (USD)` and `Estimated cost per result (AUD)`. They mirror `answers.quality_across_models` and
    `answers.cost_per_result`, including the export-rate conversion that is null when the export
    has no single rate.
  - **Gateway:** `Calls`, `Refused by gateway`, `Tokens (total)`, `Tokens per call`,
    `Cost per 1,000 calls`. They mirror `gateway.answers.cost_per_gateway_call`. `Tokens (total)`
    is not `Tokens`, because the fact has a `tokens` column *(ruling R4)*. The gateway answer gains a
    `refused` column, so `Refused by gateway` mirrors a real gold figure *(ruling R5)*.

    "Tokens per user" becomes tokens per caller label, and "cost per 1,000 requests" applies to
    the gateway session's calls. Those are the only data these measures exist for, and the page
    says so.
- **Identity:** the model reads OneLake through a **fixed identity**, a cloud connection with
  single sign-on off, bound with `fabric-cicd`'s `semantic_model_binding`.
  - Its credential is the prod workspace's identity, keyless as in project 10. A service principal
    is the fallback if the workspace identity is refused.
  - With the default single sign-on, every viewer would need read access to `lh_portfolio` and
    could read gold past the filter.
  - Test users get Read and Build on the model only, never a workspace role or lakehouse access.

### 4.2 Row-level security

- **One role, `ProjectLead`.** It filters `user_project` to the signed-in user with
  `[upn] = LOWER(USERPRINCIPALNAME())`. The model has no relationship to `user_project`, so every
  table with a `project` column carries its own filter:
  `[project] IN CALCULATETABLE(VALUES(user_project[project]), user_project[upn] = LOWER(USERPRINCIPALNAME()))`.
  The two test users are its members.
- **The filter compares with the lower-case UPN** *(ruling R8)*. The loader stores UPNs in lower
  case, and Direct Lake's collation is unconfirmed. A case mismatch would fail closed and hide
  everything from every lead.
- **The owner sees everything**, because RLS doesn't apply to workspace Admins and Members. The
  access table therefore needs no "all" row.
- **`user_project` holds UPNs, so it is never committed.**
  - A git-ignored `private/access.csv` (`upn,project`) is uploaded once to
    `Files/private/access/` (Claude, after a yes).
  - A small loader writes it to `security.user_project`. The loader lives in the package, with an
    entry point and a one-cell notebook `nb_access_load`.
  - The loader rejects an unknown project and duplicate rows. It never prints a UPN, only counts.
- What a lead sees:
  - `lead-p4`: p4's spend, an empty quality page (project 4 ran no studies), and the whole
    gateway page (`fact_gateway_call.project` is p4);
  - `lead-p3`: p3's spend and study, and an empty gateway page.

  The two sets of screenshots prove the filter on all three pages.

### 4.3 The report `rp_portfolio`

These are the three pages from the 24 Oct wireframe. The report is authored in dev's web editor
once Fabric is live, and committed back to `dev` through git sync as PBIR. It is never
hand-written.

1. **Spend by project (AUD, actual).**
   - Cards: total, untagged (share) and shared.
   - A stacked column chart of daily cost by project.
   - A table of project, cost, share and attribution rule.
   - Slicers for month, project and billing currency.
2. **Quality across models.**
   - Success rate by model and arm, with n on every bar.
   - A table of study, model, deployment version, n, score and estimated cost per result.
   - A banner: "No model changed version between measured runs: this compares models, not
     versions."
3. **Gateway health (project 10's session).**
   - Cards: calls, refusals, and cost per 1,000 calls.
   - Calls per minute by `answered_by`, with the failover window shaded.
   - Tokens per minute per caller, with the 8,000 threshold line.
   - A banner: "One measured session on [date], a demo stream, not production traffic."

### 4.4 Deploy and load changes (project 9's pipeline)

- **Rebinding.** `parameter.yml` gains a `find_replace` on the model's `definition/expressions.tmdl`.
  It swaps dev's workspace and lakehouse IDs for `$workspace.$id` and
  `$items.Lakehouse.lh_portfolio.$id` in PROD. Fabric's deployment-pipeline rules don't support
  this storage mode.
- **The connection binding.** `semantic_model_binding` binds `sm_portfolio` to the fixed-identity
  connection. Its ID comes from a prod environment secret via `$ENV:`, as in project 10.
  `fabric-cicd`'s binding is best-effort and never fails publish, so `refresh-model` verifies it:
  the model's connections must list the fixed-identity connection, or the step fails and starts no
  refresh *(ruling R7)*. The fixed identity is a security property, so the deploy fails closed.
- **Item scope.** `ITEM_TYPES_IN_SCOPE` gains `SemanticModel` and `Report`.
- **A new deploy step, `refresh-model`.** After `publish`, it frames the model through the
  refresh API, because `fabric-cicd` 1.4.0 never refreshes and new tables stay invisible until a
  refresh. It follows the same error rules as the other steps. The service principal stays the
  model's owner, so it can bind and refresh.
- **The daily pipeline** gains an `OPTIMIZE` of each gold table after the load. A Direct Lake on
  OneLake table over 1,000 Parquet files errors rather than falling back. How the model then picks
  the load up is decided in plan 2, at the `fabric/VERIFY.md` row on the model's refresh after the
  daily load: the model's automatic updates if a daily load shows up without `refresh-model`,
  otherwise a refresh after `nb_load` in the pipeline.

### 4.5 The model-versus-gold check

- `lakehouse.semantic.check_model_matches_gold(...)`, run from a notebook `nb_model_check` **by the
  owner, as a user**.
  - The service principal can't do this: `evaluate_measure` is unsupported for service principals,
    and executeQueries rejects them on RLS models.
  - It uses semantic link's `evaluate_dax`, which is preinstalled in Runtime 2.0.
- **What it checks:**
  - for each measure, its value at the grain of the gold answer it mirrors equals that answer
    (exact for counts; within 0.005 for money and rates);
  - `Cost per 1,000 calls` against 1000 × the answer's `cost_per_call`, and `Refused by gateway`
    against its `refused` column *(ruling R5)*;
  - as each test user (`effective_user_name`), only that user's project appears, and its figures
    equal the owner's figures for that project.
- **It fails** with the measure's name, the grain key and both values. It never prints a UPN; users
  are shown as their project.
- The comparison logic is pure Python over two result tables, unit-tested offline. Only the
  `evaluate_dax` call is live.

### 4.6 Tests (offline first)

- **TMDL text checks:**
  - every table in `GOLD_TABLES`, `SESSION_TABLES` and `user_project` is in the model, in Direct
    Lake mode, with `schemaName` set;
  - every measure named in 4.1 exists with a description naming its gold answer;
  - the role filters are exactly the `LOWER(USERPRINCIPALNAME())` filters in 4.2;
  - no UPN, tenant ID or URL other than the OneLake expression is in the definition. Fabric
    workspace and item GUIDs are allowed.
- `parameter.yml` rules evaluated as in project 10: the regex `find_replace` matches the
  expression exactly once.
- The access loader, the comparison logic, and the deploy's `refresh-model` step, with fakes as in
  `tests/test_deploy.py`.
- The hygiene check gains UPN patterns for the personal tenant's domain, held in the
  `HYGIENE_FORBIDDEN` secret. Plan 2 adds the domain and the test users' UPN local parts to it
  (a `fabric/VERIFY.md` row).

### 4.7 The live run (plan 2 of project 11)

1. Check the licence state.
2. Create the two test users (owner).
3. Upload and load the access table.
4. Create the fixed-identity connection and its secret.
5. Deploy to dev by git sync, then author the report in dev and commit it. Dev holds real gold
   from its own daily run. The access table is loaded there too (OneLake, never git). The gateway
   page is authored against dev's empty gateway table and checked in prod *(Claude's call; copying
   prod's session data into dev is the fallback if authoring on empty visuals is impractical)*.
6. Deploy to prod with the refresh step.
7. Grant the test users Read and Build on the model.
8. Run the check as the owner, then as each test user (via `effective_user_name`).
9. Take the "Test as role" screenshots and the three pages' screenshots, with identifiers checked.
10. Write the README section and the report.

The run finishes by about 20 December.

## 5. What it costs

- Nothing new by the hour on the trial; capacity use is recorded.
- If authoring slips past the individual trial, one month of Power BI Pro: AU$21 plus GST.

## 6. Left to the builder

- Exact DAX and visual choices within the pages above.
- Measure and display-folder names beyond those listed.
- Whether the model refreshes from the pipeline or by automatic updates: decided in plan 2, at
  the `fabric/VERIFY.md` row on the model's refresh after the daily load (4.4).
- The fixed identity's credential type, if the workspace identity is refused.
- The TMDL details Learn doesn't show in full: check them against the first model Fabric commits.

## 7. Risks and unknowns

- **The TMDL expression shape** is assembled from Learn fragments and semantic-link-labs. The
  first commit from Fabric is the authority: record differences in `fabric/VERIFY.md`.
- **Whether "Test as role" works on a Direct Lake model with a fixed identity** is unconfirmed.
  The programmatic check (4.5) is the primary evidence; the screenshots are secondary.
- **Licence:** whether the Fabric trial keeps Power BI authoring rights after the individual
  trial ends is ambiguous on Learn.
- **Preview git sync** may commit report or model files in a shape the tests don't expect: Fabric's
  shape wins, and the tests follow.
- **Gateway page data** exists only if project 10's session ran. Without it, the empty table
  keeps the model loading, the page shows its banner and empty visuals, and the README says so.
