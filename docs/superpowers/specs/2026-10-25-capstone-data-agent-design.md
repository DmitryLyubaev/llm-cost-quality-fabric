# Phase 4 capstone: a Fabric data agent, called from project 3's Foundry — design

Written 25 October 2026 · Phase 4, optional capstone · Status: draft for owner approval

Phase plan: `portfolio-plan/plan/phase-4.md`, "Optional capstone". It builds on:
- project 9's gold tables (`docs/superpowers/specs/2026-10-21-fabric-lakehouse-design.md`, this repo);
- project 11's gold answer functions and model check (`docs/superpowers/specs/2026-10-24-project-11-semantic-model-design.md`);
- project 3's Foundry agent client (`E:\Projects\foundry-workshop-agent`, public as `foundry-workshop-agent`).

The recon behind this design, done on 25 October 2026, is in `docs/research/2026-10-25-data-agent-facts.md`. It covers Microsoft Learn on Fabric data agents, Foundry Agent Service tools, the .NET SDK, licensing and pricing.

## 1. Intent

This is a CV piece for senior and AI-engineer roles. It joins the two halves of the portfolio: an agent in Microsoft Foundry answers plain-English questions about the portfolio's own spend and quality, through a Fabric data agent over project 9's gold tables. The point is that the agent's answers are **measured against gold's own figures**, not demonstrated. A frozen question set has known answers, and every answer is scored by rule, so the CV claim is "it answered N of 15 correctly", with the failures published.

### Goals

- **A Fabric data agent `da_portfolio`** over the gold lakehouse tables. Its instructions and example SQL queries are in git, and it is deployed and published by script.
- **A "portfolio analyst" prompt agent** in project 3's Foundry resource. It calls the published data agent through Foundry's **generic MCP tool**, and is run by a new small console project in project 3's repo.
- **A frozen set of 15 questions.** The expected answers are computed from gold's answer functions, and scoring is by rule.
- **Two measured routes over the same questions:** the data agent directly (scripted), and through the Foundry analyst (attended). For each route the report covers accuracy, tool calls, tokens, cost and time. It also states plainly what the Foundry layer added or lost.

### Non-goals

- **Changing the workshop agent or its frozen 6 October study.** The analyst is a separate agent.
- **A Claude route.** Foundry's MCP tool belongs to the Agent Service path. A hand-written tool loop for Claude adds little here.
- **Answering through project 11's semantic model.** That model takes no example queries and ignores the agent's instructions (recon 2). It stays an option only if a question needs a model measure.
- **Any multi-user or row-level-security story.** The lakehouse source bypasses project 11's RLS, so only the owner queries it.
- **Anything running unattended in CI against a live capacity.**

### Anti-goals: a failure even if it works

- **A published accuracy figure that comes from a hand-typed expected answer** rather than gold's own functions, or from questions changed after the freeze.
- **The paid F2 left running or paused with smoothed usage**, rather than deleted, after the capstone.
- **The data agent placed where projects 9 to 11's trial workspaces would try to sync or deploy it.** The trial can't host a data agent, so it would break their runs.
- **An identifier in the repo, a transcript or a report.** That includes tenant, subscription or Entra object IDs, UPNs, and endpoint or MCP URLs. Fabric item and workspace GUIDs may be committed (owner, 21 Oct).

## 2. Constraints

- **Projects 9 to 11's rules hold:**
  - the `fabric@` user in the personal tenant, never the work account;
  - no identifiers in public files;
  - commits as `Dmitry Lyubaev <Dmitry.Lyubaev@gmail.com>`;
  - the owner's yes before every outward step;
  - Terraform runs in WSL.
- **Paid capacity only.** A trial capacity doesn't support data agents (recon 1). The capstone needs a **paid F2**, AUD 0.5984 an hour from the Retail Prices API (recon 6). It runs only during capstone sessions and is **deleted** at the end. Pausing bills the remaining smoothed usage at once.
- **Same region.** The data agent and its sources must be on capacities in the same region (recon 1). The F2 is created in the region of the trial capacity that hosts `lh_portfolio`, which should be Australia East. Confirm that at the start of the live run.
- **Tenant settings, set by the owner:** Copilot on, and both Azure OpenAI cross-geo settings (processing and storage, Australia to US) at least an hour before the first data agent session (recon 1). Gold holds no personal data. The owner decides on these settings in the live run.
- **Identity: every live step runs as `fabric@`** *(Claude's call, after the builder check)*. The deploy service principal signs in only from GitHub Actions on `main` (OIDC), and the capstone adds no secret and no new federated credential.
  - `publish-capstone`, the snapshot, the expected answers and the direct baseline run as `fabric@`. They sign in with `az login` in a separate config directory (`AZURE_CONFIG_DIR=$HOME/.azure-fabric`), as project 9's Fabric Terraform does.
  - Foundry runs are user-delegated anyway (recon 4). `fabric@` needs Foundry User on project 3's project, plus Read on the data agent and `lh_capstone`.
  - The Azure part of `infra/capstone` (the F2) runs as the personal subscription owner. The Fabric part (the workspace) runs as `fabric@`.
- **Order and timing:**
  - **Gold first:** this comes after project 9's live run, so prod gold exists. Project 10's session is optional: gateway questions are dropped if it hasn't run.
  - **Trial end:** the trial ends about 7 January. Only the snapshot needs prod gold (4.1), so the snapshot is taken before the trial ends. The rest runs on the F2.
  - **The Power BI licence:** `fabric@`'s individual trial likely lapses about 21 December. The capstone needs no Power BI item. If the Capacity Metrics app needs a licence by then, capacity units are read from the Fabric portal's monitoring hub instead, and the report says which.
- **Two repos:**
  - **The data agent, the question set, the expected answers and the scorer** live here.
  - **The analyst console project and its run reports** live in `foundry-workshop-agent`.
  - Each repo has its own PRs.

## 3. Approaches

| | What | For | Against |
|---|---|---|---|
| **A (chosen)** | Foundry's **generic MCP tool** in the analyst prompt agent, pointed at the published data agent's MCP endpoint through a project connection. It uses project 3's current .NET SDK (Azure.AI.Projects 2.0.1) | Keeps project 3's working client and package pins; MCP and the data agent's MCP endpoint are both documented | This combination isn't documented end to end, so a short live spike comes first |
| B (fallback) | Foundry's **Fabric IQ tool** (`fabric_iq_preview`), Microsoft's replacement for the retired data agent tool | The intended route | Preview, and it needs Azure.AI.Projects 3.0.0-beta, which reopens project 3's OpenAI package pin |
| C | **No Foundry:** query the data agent directly only | Simplest and fully scriptable | Drops "called from project 3", which is half the CV point |
| — | Foundry's "Microsoft Fabric data agent" tool | — | **Retired 6 November 2026** (recon 4), so not an option |

The owner chose **A, with B as the fallback and C kept as the direct baseline**, on 25 October 2026. If A's spike fails, the design switches to B, and everything else below stays the same.

## 4. Design (decided)

### 4.1 The capstone workspace and the F2

- **A new Terraform stack, `infra/capstone/` in this repo.** It creates three things:
  - the F2 (`azurerm_fabric_capacity`), in the trial capacity's region, administered by `fabric@`;
  - a resource group `rg-p12-capstone`, so cost attribution has a rule. The seed gains `rg-p12-*` → `capstone`, with evidence.
  - the workspace `capstone` (`microsoft/fabric` provider), with the F2 assigned and `fabric@` as its admin. Every live step runs as `fabric@`, so the deploy service principal needs no role here.

  It is applied at the start of a capstone session and destroyed at the end. *(My call: the F2 and the workspace are separate from projects 9 to 11, so their trial runs never see a data agent.)*
- **The source is a frozen snapshot of gold** *(Claude's call, after the builder check)*. Prod gold changes every day: the daily load, new cost lines, and the capstone's own spend under the `rg-p12-*` rule. So on the day of the freeze, a notebook copies prod `lh_portfolio`'s `gold` tables once into a lakehouse `lh_capstone` in the capstone workspace. The copy is a plain Delta copy, with the snapshot time recorded. The data agent reads only `lh_capstone`, and the expected answers are computed from the same snapshot. This settles the cross-workspace and cross-capacity question. After the snapshot, the capstone doesn't depend on the trial, so it can finish even after the trial ends. Moving prod to the F2 is out of scope.

### 4.2 The data agent `da_portfolio`

- **Its own folder:** the definition lives in `fabric-capstone/da_portfolio.DataAgent/`, never in `fabric/`. That keeps it out of dev git sync and out of the prod deploy (anti-goal 3). It is deployed by a separate `.deploy/deploy.py` command, `publish-capstone`, which:
  - runs fabric-cicd with that folder as the repository directory and the `DataAgent` item type;
  - rebinds each `datasource.json`'s `workspaceId`/`artifactId` with `find_replace` regexes (recon 3);
  - then calls the **Publish Data Agent** REST API, because an unpublished agent can't be reached by MCP or Foundry.

  It is run by hand, with environment variables, never by `deploy.yml`.
- **The source:** the gold lakehouse tables only, at most 25 rows per answer. The selected tables are `fact_daily_cost`, `fact_eval_attempt`, `fact_live_check`, `fact_gateway_call`, `dim_project`, `dim_study`, `dim_arm`, `dim_model_deployment` and `dim_date`.
- **Instructions:** what each table is, the grain lines from the README, and the portfolio's rules:
  - costs stay in the billing currency;
  - estimated and actual cost are never summed;
  - "untagged" and "releaselens-shared" are projects;
  - model versions didn't change between measured runs, so the agent says so when asked.
- **Example queries:** about 20 `gold.`-qualified SQL examples, validated by the SDK validator (recon 2). Each is tested offline: it runs on the fixture lake through Spark SQL and returns what its paired gold answer function returns. None of them is one of the frozen questions or a near-copy of one, so the score isn't inflated.

### 4.3 The portfolio analyst (project 3's repo)

- **The two-repo contract** *(Claude's call, after the builder check)*:
  - The analyst's instructions live in project 3, in `src/Portfolio.Analyst/instructions.md`.
  - This repo's freeze script reads that file from a pinned commit of project 3 and includes its hash.
  - `Portfolio.Analyst` takes a copy of `freeze.json` and `questions.json`. It refuses to run if its instructions, the questions or the model differ from the freeze, as project 3's `run --frozen` does.
  - It writes JSONL records. They are copied into this repo's `capstone/runs/foundry/` and scored here by the same scorer as the direct route.
  - Project 3's PR (the analyst) merges first, then this repo's PR (the freeze and the runs).
- **Where it lives:** a new console project `src/Portfolio.Analyst`, beside `Workshop.Agent`. It reuses Workshop.Agent's Foundry client setup and keyless sign-in, and the same Azure.AI.Projects 2.0.1 pin.
- **The agent:** a prompt agent named `portfolio-analyst`, with short instructions:
  - answer only from the tool;
  - say "not in the data" when the tool can't answer;
  - give figures with their currency and sample size.

  Its one tool is the MCP tool to the data agent, through a project connection with user-delegated auth.
- **The model:** `gpt-5.6-luna` if Foundry's tool-support table lists it for MCP, otherwise `gpt-5.5` (recon 4). It is re-checked on the day.
- **What it records per question:**
  - the raw tool output (recon: no doc promises the generated SQL);
  - the final answer;
  - tokens, tool calls and wall time.

  Records are written as JSONL with project 3's existing redaction, so no URL or ID is kept.
- **Project 3's `infra/foundry` stack** is re-applied for the run and destroyed after it, by its own runbook.

### 4.4 The question set and scoring (this repo)

- **The questions:** `capstone/questions.json` holds 15 questions in plain English, each with an `id`, a `kind`, a scoring `rule` and the gold answer function plus arguments that produce its expected answer. The spread:
  - spend: by project, untagged, one month;
  - quality: success rate by model, top-1 by arm for project 2;
  - cost per result;
  - live checks: pass and fail counts;
  - the gateway session, if it ran;
  - one join, one date filter;
  - one unanswerable question ("quality by model version"), whose right answer is a refusal or caveat.
- **Where expected answers come from** *(Claude's call, after the builder check)*. Each question names one of two kinds of source, and none is ever a typed value:
  - an existing gold answer function plus a **selector** (a row filter and a column) over its output, for example `cost_per_project` → project `untagged`, currency `AUD`, column `total`;
  - or a new, tested function in `lakehouse.capstone.answers`, where none exists. Examples: spend for one closed month, live-check pass and fail counts, a joined or date-filtered figure. Each new function has a unit test on the fixture lake.
- **Expected answers:** computed by a notebook `nb_capstone_expected` in the capstone workspace from the `lh_capstone` snapshot, run by `fabric@`. It runs the named functions and selectors and writes `expected.json` (figures and labels only). A copy is committed after an identifier check.
- **The freeze** *(widened after the builder check)*. `capstone/freeze.json` holds one hash over:
  - the questions, the expected answers, the scoring rules and the snapshot time;
  - the data agent's definition (instructions and example queries);
  - the analyst's instructions (from project 3);
  - both model choices.

  This follows how project 3's study froze its scenarios.
- **No tuning after the freeze.** The data agent and the analyst may be tuned only before it, against a separate practice set of about 10 questions (`capstone/practice.json`). That set is never scored or published, and none of it overlaps the frozen 15. Any change after the freeze means a new freeze and new runs, and the report says how many freezes there were.
- **Scoring:** `lakehouse.capstone.score(answer_text, expected, rule) -> Verdict` is pure Python, by rule, with no AI judge:
  - money within 1% or 0.01 of the billing currency;
  - counts exact;
  - rates within 0.005;
  - sets of labels equal;
  - "unanswerable" passes only on a refusal or an explicit caveat.

  Numbers are extracted from the answer text by a documented parser. An answer the parser can't read is wrong, never "maybe".
- **Microsoft's `evaluate_data_agent` is not used** *(Claude's call, after the builder check)*. It asks the agent itself, so it could only grade an extra pass in a notebook, at extra F2 cost. The rule-based scorer is the only judge.

### 4.5 Runs and evidence

- **Direct:** 3 passes of the frozen 15 questions through the data agent's MCP endpoint, scripted by `tools/ask_data_agent.py` (development tooling in this repo) and run as `fabric@`.
- **Through Foundry:** 3 passes through `Portfolio.Analyst`, attended.
- **The report**, `capstone/report.md` in this repo, is rendered from the records by a script. It gives:
  - accuracy per question and overall, for each route;
  - tool calls, tokens and cost per question;
  - F2 CU from the Capacity Metrics app;
  - every wrong answer quoted.

  The verdict comparing the two routes is descriptive at 15 questions: no claim of "better" without a margin.
- **The headline figures** *(Claude's call, fixed before the freeze)*, for each route:
  - mean correct per pass, out of 15;
  - questions correct in all 3 passes;
  - questions correct in none.
- **Cost:**
  - **The Foundry route** has tokens and cost per question from its records.
  - **The direct route** returns no token counts, so its cost is reported per run: F2 capacity units from the Capacity Metrics app over the run's time window, plus wall time per question. Each run is kept in its own clear time window, so the windows don't overlap.
- **The READMEs:** both repos gain a short capstone section linking to the report.

### 4.6 Tests (offline first)

- **The scorer:** every rule, with edge cases (currency symbols, thousands separators, a refusal, no number, a wrong-but-close figure).
- **The expected-answer builder** on the fixture lake.
- **Every example SQL query** checked against its paired gold answer function on the fixture lake.
- **The freeze hash** is deterministic.
- **The deploy:** `publish-capstone` with fakes (the regexes, the Publish call, status before body, no IDs in output).
- **The data agent definition:** the instructions name only real tables and columns, and the definition has no identifiers.
- **Project 3:** the analyst's record writer, redaction, and the parsing of a canned tool output, with no live calls.

### 4.7 The live run (plan 2 of the capstone)

The order:
1. Check region and capacity, then the owner's tenant settings (Copilot, cross-geo).
2. Apply `infra/capstone` (F2 and workspace), with the owner's yes, because it bills.
3. Snapshot gold into `lh_capstone`, then run `publish-capstone`.
4. **The spike:** one question through Foundry's MCP tool. If it fails, switch to approach B.
5. Compute the expected answers, then freeze.
6. Run the direct passes, then the Foundry passes.
7. Render the report and check it for identifiers.
8. Destroy the F2 and project 3's Foundry stack, and check that both are gone.
9. Write the README sections and update the tracker.

## 5. What it costs

- **The F2:** AUD 0.60 an hour while it exists. The data agent's queries bill as Copilot "AI query" usage inside the F2's capacity units (recon 6). Measure the first run before planning repeats. A likely total is a few hours.
- **Foundry:** model tokens only, a few cents a run at `gpt-5.x` prices.
- **Totals:** both are recorded after the run, from the cost export (the `rg-p12-*` rule).

## 6. Left to the builder

- The exact questions within the spread above, and the exact example queries.
- The number parser's details within the documented rules.
- `Portfolio.Analyst`'s structure, within project 3's conventions.
- The `capstone` workspace's Terraform details.

## 7. Risks and unknowns

- **Approach A's MCP connection** (user-delegated auth to the data agent's MCP endpoint, from Azure.AI.Projects 2.0.1) is undocumented end to end. The spike settles it, and B is the fallback.
- **The snapshot copy:** it reads prod `lh_portfolio` from a notebook in the capstone workspace, across workspaces and capacities in the same region. This is untested, and a copy run in prod's own workspace and written to `lh_capstone` by path is the fallback.
- **Tool output:** it may not include the generated SQL, so the records keep whatever is returned. The data agent's diagnostics export is the fallback for reading the SQL it generated.
- **Preview drift:** data agent git is still marked preview on its own page, and the Fabric IQ fallback is preview. Fabric's committed form wins over the hand-written one.
- **Model availability:** the tool-support table may change. The model is re-checked on the day.
