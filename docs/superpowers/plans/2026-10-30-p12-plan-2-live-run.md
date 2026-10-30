# Project 12 (the phase 4 capstone), plan 2: the live runbook

> **For the session running this:** a runbook, not a build plan, apart from three offline tasks
> (steps 2–3 TDD, step 17 docs). Execute it inline (superpowers:executing-plans), one step at a
> time. **Every step marked 🔒 waits for the owner's explicit yes in chat for that step**; a yes for
> one step never carries to the next. Steps marked 👤 are done by the owner in a browser. Claude
> never enters passwords, creates accounts or users, assigns Entra roles, changes tenant settings,
> accepts terms or creates tokens.

**Goal:** Run the capstone's measured comparison once:
- a frozen 15-question set;
- 3 passes direct to the data agent and 3 through project 3's portfolio analyst;
- every answer scored by rule against gold's own figures.

Then publish the report and delete everything that bills.

**Spec:** `docs/superpowers/specs/2026-10-25-capstone-data-agent-design.md` (§4.7 is this run).
**Built by:** plan 1, merged as this repo's PR #12 (`3dd8b3b`) and foundry-workshop-agent #9
(`de1e8e6`).

**Format checks:**
- `fabric-capstone/VERIFY.md` (data agent, lakehouse, notebooks, publish);
- `infra/capstone/README.md` (the two stacks, the order and the destroy order);
- project 3's `src/Portfolio.Analyst/VERIFY.md` (what the MCP spike must verify).

## Rules for the whole runbook

- **Project 9's rules apply unchanged.** See `docs/superpowers/plans/2026-10-23-plan-3-live-runbook.md`, "Rules for the whole runbook":
  - the sign-ins: the personal Azure owner, and `fabric@` in Fabric (Terraform's Fabric stack uses `AZURE_CONFIG_DIR=$HOME/.azure-fabric`);
  - never the work account;
  - the Norton CA bundle per process;
  - identifiers only in git-ignored files, secrets or private logs;
  - record everything;
  - a tracker line after each phase.
- **Run log:** `private/live/p12-run-log.md` (UTC time, what ran, the outcome). Evidence stays in `private/live/p12/` until step 17 checks it.
- **Money:** the F2 bills about A$0.60 an hour, from step 5 until step 16.
  - Data agent queries bill inside it as Copilot "AI query" usage.
  - Pausing bills any smoothed usage at once.
  - Run phases B and C on one day, or two consecutive days with the F2 **paused** overnight (the owner's choice, recorded).
  - Step 16 deletes it.
- **The freeze is the line.** Nothing that decides a score may change after step 12: `score.py`, the questions, the expected answers, the data agent's definition, the analyst's instructions, or either model. A change after it means a new freeze, new runs, and the report counts the freezes.
- **Practice only before the freeze.** Tuning uses `capstone/practice.json` alone. The frozen 15 are never asked before step 13.
- **Endpoints and IDs:** `CAPSTONE_WORKSPACE_ID`, `CAPSTONE_MCP_URL`, `ANALYST_MCP_URL`, `ANALYST_CONNECTION_ID` and `FWA_PROJECT_ENDPOINT` are set in the process only and never printed. The runners already refuse to print them.

## Preconditions

- **Project 9:** plan 3 has run, so prod gold exists and loads daily.
- **Project 10:** its session has run if q12 (the gateway question) is to be kept. Otherwise q12 is dropped (step 8).
- **The trial:** it has not ended (about 7 January 2027), because step 7 reads prod's `lh_portfolio`. After the snapshot, nothing depends on the trial.
- **Project 3:** its `infra/foundry` stack can be re-applied by its own runbook (`docs/runbook-plan-3.md`).

---

## Phase A — Offline, before any F2 (the plan 2 gates from plan 1's reviews)

### Step 1: Read-only preflight (Claude)

- **Azure and money:** `az account show` (personal user), the A$10 budget, and `az group exists -n rg-p12-capstone` (must be `false`).
- **Resource provider:** `az provider show --namespace Microsoft.Fabric --query registrationState`. Record it. Registering it is step 5's first action.
- **Region:** read the trial capacity's region in Fabric's admin portal (owner's screen 👤). Set `location` in `infra/capstone/capacity/terraform.tfvars` to it; it's `australiaeast` unless the portal says otherwise.
- **Tenant settings 👤:** read whether Copilot and both Azure OpenAI cross-geo settings (processing and storage) are on. Don't change them yet.
- **Repo state:** both repos' `main` are clean and current. Authors are personal-only.

### Step 2: Close the scorer gaps, and add the value-based leak check (offline TDD, this repo)

**Files:** `src/lakehouse/capstone/score.py`, `tests/test_capstone_score.py`,
`src/lakehouse/capstone/leaks.py`, `tests/test_capstone_leaks.py`,
`fabric-capstone/nb_capstone_expected.Notebook/notebook-content.py`, `fabric-capstone/VERIFY.md`.

1. **Unanswerable gaps.** Close the claims of an unnamed direction that still pass:
   - "It changed a lot";
   - "It stayed flat across versions";
   - "Even if the data has no version history, quality went up or down noticeably".

   **The change:**
   - add `changed`, `change`, `flat`, `stayed`, `steady`, `stable` and `unchanged` to `CHANGE_WORDS`, unless they're inside "didn't change" or "no change" next to "version";
   - make the whether/if either-or exemption require a refusal verb in the same clause, so "even if … up or down" no longer qualifies.

   **Tests:** these three answers fail. Every refusal that passes today still passes, including the instructions' own example "Not in the data: every model ran on a single version, so quality by version can't be shown". Re-run the existing probe cases.
2. **A value-based leak check:** `value_leaks(spark, paths, questions, fewshots_sources) -> list[dict]`.
   - It evaluates every example's Source on the given lake.
   - For each single-cell frozen question, it reports any example output row whose columns include the question's selector keys with equal values, and whose figure column equals the expected value (within the scorer's tolerance).
   - It returns findings as `{example_id, question_id}` only.

   **Tests on `loaded_lake`:**
   - a synthetic example built to equal q02 is caught;
   - the shipped examples report none on the fixture.

   **Wire it in:** `nb_capstone_expected` runs it after `compute_expected` and writes `leaks.json` beside `expected.json`. Step 8 stops if `leaks.json` is not empty.
3. **Run and merge.** Run `tests/test_capstone_score.py`, `tests/test_capstone_leaks.py`, `tests/test_capstone_agent.py`, `tests/test_capstone_freeze.py` and `tests/test_hygiene.py`. Open a branch and PR, then merge on the owner's yes 🔒 and cancel the deploy run.

### Step 3: Align the analyst's refusal words with the scorer (offline, project 3)

- **Instructions:** in `src/Portfolio.Analyst/instructions.md`, make the refusal line list exactly the scorer's `CHANGE_WORDS` (after step 2), not "such as" examples. Pin it with a test that reads the list from a copied `capstone/change-words.json`.
  - That file is written by this repo's step 2 from `CHANGE_WORDS`.
  - It is copied byte for byte into project 3, and both repos' tests check their copy.
- **Ship:** open a PR in project 3 and merge it on the owner's yes 🔒. Record the merge commit: step 12 pins the freeze to it.

---

## Phase B — The F2 day: build, check, compute (F2 bills from step 5)

### Step 4: Tenant settings 👤 (at least an hour before step 6)

- **Turn on:** the owner turns on Copilot and both Azure OpenAI cross-geo settings (processing and storage), scoped as narrowly as the portal allows: a security group containing `fabric@`.
- **Record:** the before and after states go in the run log.
- **Stop** if the owner declines. The data agent can't run without them (recon §1).

### Step 5: The F2 and the workspace 🔒

- **Register the provider:** `az provider register --namespace Microsoft.Fabric`, then wait for `Registered`.
- **Capacity**, as the personal owner, in WSL:
  - `terraform -chdir=infra/capstone/capacity init`, then `plan -out=tfplan`;
  - show the plan, which must be one resource group plus the F2 `capstonef2`, SKU `F2`;
  - `apply tfplan` on the owner's yes;
  - **billing starts: record the UTC time.**
- **Workspace**, as `fabric@` (`AZURE_CONFIG_DIR=$HOME/.azure-fabric`):
  - `terraform -chdir=infra/capstone/workspace init`, then `plan` and `apply`;
  - if the lookup's postcondition rejects the capacity, record the real `sku` and `state` values, fix the literals on a branch, and set `capacity_id` to proceed meanwhile.
- **Check 👤:** the workspace `capstone` shows on the F2, with `fabric@` as admin.

### Step 6: Deploy and check the data agent 🔒

- **The wheel 👤 or 🔒:** upload `lakehouse-<version>-py3-none-any.whl`, from `python -m build --wheel` on `main`, to `lh_capstone`'s `Files/wheels/`. This happens after the first publish creates `lh_capstone`, so run `publish-capstone` once, upload, and run it again.
- **Publish:** `CAPSTONE_WORKSPACE_ID=… python .deploy/deploy.py publish-capstone`, as `fabric@` in the `.azure-fabric` shell. Expect `publish-capstone: ok`.
- **Definition format:** export `da_portfolio`'s definition (Get Item Definition) to `private/live/p12/` and diff it against `fabric-capstone/da_portfolio.DataAgent/`. Resolve every format row in `fabric-capstone/VERIFY.md`: the folder casing, `$schema`, the `elements` shape and the `fewShots` key. Where Fabric differs, take Fabric's form on a branch, update the tests, open a PR, merge on a yes, and re-publish. The definition is hashed, so this must land before the freeze.
- **Example SQL:** in a notebook in the capstone workspace, run `evaluate_few_shots` on the deployed agent 👤. Any example Fabric rejects is fixed on that same branch, with its Source test still passing.
- **Record** the commit `publish-capstone` ran from (ruling R6: the published description sits outside the freeze).

### Step 7: The snapshot 🔒 (outside prod's daily-load window)

- **When:** check that prod's `pl_daily_load` isn't running and won't start within 30 minutes. It runs daily at 06:00 Brisbane.
- **Run 👤:** run `nb_capstone_snapshot` with `source_workspace_id` and `source_lakehouse_id` set at run time, and never saved to git (VERIFY row). It copies all 9 gold tables at pinned versions.
- **Check:** `Files/capstone/snapshot.json` has the snapshot time, one Delta version per table, and row counts matching prod's.
- **Commit:** download `snapshot.json` to `capstone/snapshot.json` and commit it on the run branch.

### Step 8: The expected answers, and the leak check 🔒

- **q12, the gateway question:** if project 10's session ran, set q12's `args.session` and its text to the real session name. Otherwise remove q12 from the upload; it is optional, and the report counts 14. This change to `questions.json` lands on the run branch before the upload.
- **Run:** upload `questions.json` and `fewshots-sources.json` to `lh_capstone`'s `Files/capstone/`, then run `nb_capstone_expected` 👤.
- **Collect:** download `expected.json` and `leaks.json`.
- **Stop** if `leaks.json` lists anything. Fix the leaking example on a branch, re-publish (step 6), and re-run this step.
- **Commit:** check `expected.json` for identifiers (figures and labels only), then commit it to `capstone/expected.json` on the run branch.

### Step 9: Foundry and project 3 🔒

- **The stack:** re-apply project 3's `infra/foundry` stack by its runbook. That's model tokens only; no hourly cost.
- **The model:** check Foundry's tool-support table for MCP on the planned model, `gpt-5.6-luna`. If it isn't listed, use `gpt-5.5`, and record which.
- **Access 👤:** give `fabric@` **Foundry User** on project 3's Foundry project. That's an Entra role assignment, so the owner makes it.
- **The connection 👤:** create a Foundry project connection to the data agent's MCP endpoint, user-delegated, per recon §4. Record its name and ID in the run log only.

### Step 10: The MCP spike: one practice question each way 🔒

- **Direct:** run `python tools/ask_data_agent.py` on **one practice question** (a practice-mode run that writes to `private/live/p12/spike/`, not `capstone/runs/`). Check against project 3's VERIFY points and this repo's runner notes:
  - the tool's question argument;
  - the protocol version;
  - `isError` on a refusal;
  - the shape of the answer.
- **Foundry:** run `portfolio-analyst` on the same practice question, in the same spike mode. Check:
  - that `project_connection_id` is accepted, and whether it wants the connection's name or full ID;
  - that Foundry echoes the tool settings for the drift check;
  - that the answer arrives.
- **Fixing:** fix any mismatch on a branch in the repo it belongs to, with tests, a PR, and a merge on a yes.
- **Fallback:** if the analyst can't attach the MCP tool at all on Azure.AI.Projects 2.0.1, stop. The owner decides on approach B, the Fabric IQ tool on the 3.0.0 beta SDK, which is a new spec decision.

### Step 11: Practice tuning (practice set only)

- **Run:** both routes, 1 pass each, on `practice.json`.
- **Tune:** adjust the data agent's instructions and examples, or the analyst's instructions, only from what the practice answers show. Each change goes through a branch, tests, a PR and a merge on a yes, followed by a re-publish.
- **Limit:** at most two tuning rounds, recorded. The frozen 15 are never asked.

---

## Phase C — Freeze and measured runs

### Step 12: The freeze 🔒

On the run branch, run `python tools/capstone_freeze.py freeze` with:
- `--expected capstone/expected.json`;
- `--snapshot-time`, taken from `snapshot.json`;
- `--data-agent-dir fabric-capstone/da_portfolio.DataAgent`;
- `--analyst-repo E:\Projects\foundry-workshop-agent --analyst-commit <step 3's merge commit or later>`;
- `--model-direct` and `--model-foundry`.

Then:
- commit `capstone/freeze.json`;
- run `verify`;
- run `portfolio-analyst verify` with the same `freeze.json` and `questions.json` (exit 0).

**From here nothing that decides a score changes.**

### Step 13: The direct passes 🔒

Run `python tools/ask_data_agent.py --passes 3 --out capstone/runs/direct` as `fabric@`.
- It refuses to start unless the freeze verifies.
- Record each pass's UTC start and end, for the CU window.
- **Stop:** an exit of 3 (a tool error) stops the run. Record it, find the cause (for example a paused capacity), and re-run only that pass, after renaming its `.partial` file.

### Step 14: The Foundry passes 🔒

- **Run:** `portfolio-analyst run --freeze … --questions … --passes 3 --out <dir> --model <frozen model>`, signed in as `fabric@`, attended. Record the windows.
- **Collect:** copy the JSONL records into `capstone/runs/foundry/`.
- **Stop:** exit 3 is handled as in step 13.

### Step 15: CU and the report

- **CU:** read each route's capacity units for its time windows from the Capacity Metrics app 👤. If it can't be installed after the Power BI trial ends, use Fabric's monitoring hub, and the report says which.
- **Render:** `python tools/capstone_report.py --out capstone/report.md --freezes <n> --cu-direct … --cu-foundry … --cu-unit "CU-seconds"`, from a clean tree. It prints the commit, and refuses to run if `score.py` or `report.py` is dirty.
- **Check:** look for identifiers. Records are already redacted; check the report anyway.

---

## Phase D — Teardown and publish

### Step 16: Delete what bills 🔒

- **Workspace first:** `terraform -chdir=infra/capstone/workspace destroy`, as `fabric@`.
- **Then capacity:** `terraform -chdir=infra/capstone/capacity destroy`, as the owner. If it is refused, use `-refresh=false` as the README says. **Billing stops: record the UTC time.**
- **Check:** `az group exists -n rg-p12-capstone` gives `false`.
- **Project 3:** destroy its `infra/foundry` stack by its runbook.
- **The connection 👤:** delete the Foundry project connection.
- **Tenant settings 👤:** the owner decides whether to turn Copilot and the cross-geo settings off again, and the decision is recorded.

### Step 17: Publish 🔒

- **This repo:** a PR from the run branch holding:
  - `capstone/snapshot.json`, `expected.json` and `freeze.json`;
  - `runs/direct` and `runs/foundry`;
  - `report.md`;
  - a README capstone section: the headline figures per route, the dates, the commits, the limits, and that this is 15 questions, so descriptive only;
  - every VERIFY row resolved, in `fabric-capstone/VERIFY.md` and project 3's VERIFY.

  Merge on a yes, and cancel the deploy run.
- **Project 3:** a PR adding a README section that links to the report. Merge on a yes.
- **Cost:** after the cost export catches up (1–2 days), add the F2's AUD cost to the report from gold `fact_daily_cost` (project `capstone`, rule `rg-p12-*`), in a follow-up commit.

### Step 18: Close

- **Tracker:** project 12 is done.
- **Checks:** both resource groups are absent, and there's no Foundry connection left.
- **Run log:** closed.

---

## Review Focus (what most likely goes wrong live)

1. **The MCP connection from Azure.AI.Projects 2.0.1** is unproven. Step 10 settles it before the freeze, and fallback B is a new owner decision, not an improvised upgrade.
2. **Tenant settings off, or the wrong region:** the data agent won't build or run. Steps 1 and 4 come first, at least an hour ahead.
3. **The snapshot mixing two loads, or answers leaking into examples on real data.** Step 7 avoids the load window. Step 8's value-based leak check stops the run.
4. **Anything changed after the freeze.** Every runner verifies the freeze. The report counts freezes and refuses dirty scoring code.
5. **The F2 left running.** Step 16 destroys both stacks and checks the resource group is gone. Pausing between days is the only exception, and it's recorded.
