# Project 9, plan 3: the live runbook

> **For the session running this:** this is a runbook, not a build plan. Execute it inline
> (superpowers:executing-plans), one step at a time. **Every step marked 🔒 waits for the owner's
> explicit yes in chat for that step.** Approval for one step never carries to the next. Steps
> marked 👤 are done by the owner in a browser; Claude never enters passwords, creates accounts,
> starts trials, accepts terms or creates tokens.

**Goal:** Take the merged offline work (`main` at `98cbcb9` or later) live: the cost export, the
Fabric trial and workspaces, the public GitHub repo, the deploy identity, dev and prod running
daily, the quality checks shown failing on purpose, capacity measured, and the three dated
answers published.

**Spec:** `docs/superpowers/specs/2026-10-21-fabric-lakehouse-design.md` (section 11 governs).
**Facts:** `docs/research/2026-10-22-fabric-facts.md`. **Format checks:** `fabric/VERIFY.md`.

## Rules for the whole runbook

- **Identity.** Two sign-ins, never the work account:
  - *Azure:* the owner's personal account, the subscription owner. Before every `az` command run
    `az account show --query user.name -o tsv`; stop if it ends in the employer's domain.
  - *Fabric:* a work-or-school user in the personal tenant (`…@<tenant>.onmicrosoft.com`). For
    Terraform's Fabric stack, `az login` runs as that user in a separate config directory
    (`AZURE_CONFIG_DIR=$HOME/.azure-fabric`), so the two sign-ins never mix.
- **Norton:** every `az`, `terraform` and `pip` command on this PC gets the CA bundle for that
  process only (`REQUESTS_CA_BUNDLE` / `SSL_CERT_FILE` =
  `%USERPROFILE%\.azure\ca-bundle-with-norton.pem`). Read status codes before bodies.
- **Identifiers.** Tenant and subscription IDs, the storage account name, the export location and
  connection IDs go only into git-ignored `terraform.tfvars`, GitHub environment secrets, or the
  private run log `private/live/run-log.md`. Never into chat summaries, commits or public docs.
- **Record everything, including failures.** Each step appends to `private/live/run-log.md`: UTC
  time, what ran, the outcome, and anything unexpected. The public record
  (`docs/live-report.md`, written in step 17) is built from it with identifiers removed.
- **Stop conditions** are listed per step. On any stop: record it, say so plainly, and wait.
- **Tracker:** after each phase, append a line to `portfolio-plan/TRACKER.md` and push (approved).

---

## Phase A — Before anything bills or goes public

### Step 1: Read-only preflight (Claude)

- `az account show` (personal user; subscription is pay-as-you-go).
- `az consumption budget list` — the A$10 budget alert exists and is enabled.
- `az group list --query "[].name"` — note whether `rg-releaselens-bootstrap`, `rg-fwa-bootstrap`
  and `rg-fwa-foundry` still exist (spec section 4, "still to check"). Read only; change nothing.
- `git -C E:/Projects/Fabric log --format='%an <%ae>' | sort -u` — only the personal address.
- **Record** the findings. **Stop** if the user is the work account or no budget exists.

### Step 2: The history decision 👤

The spec's earlier wording named the employer's email domain. It was added in `78f6c2b` and
removed in `11c1696`, so every commit in between still carries it. The owner decides:
- **(a)** rewrite local history before the first push (`git filter-repo --replace-text`, run by
  Claude after a yes, with a backup branch kept locally), or
- **(b)** push as is.

Nothing in this runbook pushes until this is answered.

---

## Phase B — The cost export (first, because data takes up to a day)

### Step 3: Cost-export storage 🔒 (Claude runs; owner approves amendment 6)

- The owner confirms amendment 6: **public network access on, shared keys off, Entra-only.**
  If refused, **stop the runbook** and revisit the design.
- Create `infra/cost-export/terraform.tfvars` (git-ignored) with `subscription_id`,
  `storage_account_name` (a new globally unique name), `reader_principal_ids = []`.
- `terraform -chdir=infra/cost-export init`, `plan -out=tfplan`. Show the plan summary: expect
  **3 to add** (resource group, storage account, container), 0 change, 0 destroy.
- 🔒 `terraform apply tfplan`. Check: `az storage account show --query
  "{hns:isHnsEnabled, keys:allowSharedKeyAccess, tls:minimumTlsVersion}"`.
- **Note the review's warning:** if the provider can't read data-plane properties with shared keys
  off, grant the owner Storage Blob Data Contributor on the account, then re-apply.

### Step 4: The exports 🔒 (owner in the portal, or Claude by REST after a yes)

- Export 1, **daily, month-to-date, Actual cost**, CSV, gzip, "Overwrite data" on, into
  container `cost-exports`, root folder `exports`.
- Export 2, **one-time, Actual cost, 1–30 September 2026 only** (one calendar month per export;
  a range spanning two months now fails the load by design).
- If the export can't write with shared keys off, **stop** and bring the error to the owner
  (options: managed identity per the exports doc, or a design change). Do not turn keys on.
- After the first run lands (up to 24 h), **record** the real header, a manifest's `blobName`
  layout and the folder names. Compare with `COST_COLUMN_ALIASES` and R5's blob resolution; fix
  offline (TDD, own commit) if they differ.
- If September never produces rows, **record** "costs before the 30 September offer change are
  unavailable" — the spec's 3.5 says the window shrinks and the README says so.
- `COST_EXPORT_SUBPATH` = `/cost-exports/exports` (the container plus the root folder, never the
  export-name folder itself); `COST_EXPORT_LOCATION` = the account's DFS URL. Both go to GitHub
  secrets in step 9, nowhere else.

---

## Phase C — Fabric

### Step 5: The Fabric user and tenant settings 👤

- If no work-or-school user exists in the personal tenant: create one in Entra
  (`fabric@<tenant>.onmicrosoft.com`) and give it the **Fabric Administrator** role.
- Sign in to Fabric as that user. **Stop** if Fabric refuses the user; never fall back.
- Tenant settings (exact names from research §2), each on for the whole organisation or a group
  holding the user and the deploy service principal:
  - *Users can create Fabric items*; *Users can try Microsoft Fabric paid features*
  - *Users can sync workspace items with GitHub repositories* (off by default)
  - *Service principals can call Fabric public APIs*
- **Record** each setting with its state before and after.

### Step 6: Start the trial 🔒👤

- Account manager → **Start trial**, region Australia East. **The 60 days start now.**
- **Record** the start time and whether it is F4 or F64. If F4 and *Change size* is offered,
  the owner decides whether to go to F64.
- Install the Capacity Metrics app (research §1) now, so it collects from day one.

### Step 7: The deploy identity 🔒 (Claude runs as the personal Azure user)

- `az ad app create --display-name p9-fabric-deploy`; `az ad sp create --id <appId>`.
- Two federated credentials, issuer `https://token.actions.githubusercontent.com`, audience
  `api://AzureADTokenExchange`, subjects
  `repo:DmitryLyubaev/llm-cost-quality-fabric:environment:dev` and `…:environment:prod`.
- No client secret, no Azure role, no Entra API permissions (the Fabric tenant setting governs).
- **Record** the app and object IDs privately.

### Step 8: Workspaces 🔒 (Claude runs Terraform as the Fabric user)

- `AZURE_CONFIG_DIR=$HOME/.azure-fabric az login --tenant <tenant> --allow-no-subscriptions` as
  the Fabric user (the owner completes the browser sign-in).
- `infra/fabric/terraform.tfvars`: `deploy_sp_object_id` from step 7.
- `init`, `plan -out=tfplan`: expect **4 to add** (2 workspaces, 2 role assignments).
- 🔒 `apply tfplan`. **If the workspace identity fails without a capacity** (the review's
  warning): apply with `-target` the workspaces without identity, assign the capacity (next
  bullet), then apply again.
- 👤 Assign the trial capacity to `p9-dev` and `p9-prod` in each workspace's settings.
- 👤 In both workspaces: Spark settings → **Runtime 2.0**; high concurrency for pipelines
  **off** (`%pip` is unsupported there).
- `terraform output` → the two workspace identity service-principal IDs → put them in
  `infra/cost-export/terraform.tfvars` `reader_principal_ids`, then 🔒 plan (expect 2 to add) and
  apply the cost-export stack again.
- 👤 One Fabric connection: Azure Data Lake Storage Gen2, the account's DFS URL, auth **Workspace
  identity**. Share it with the deploy service principal (User). Its ID → `FABRIC_CONNECTION_ID`.

---

## Phase D — GitHub and the first deploy

### Step 9: The public repo 🔒 (Claude runs `gh` as `DmitryLyubaev`)

- Only after step 2 is answered.
- `gh repo create DmitryLyubaev/llm-cost-quality-fabric --public --source . --remote origin`
  (no push yet).
- Secret scanning push protection on. Repository secret `HYGIENE_FORBIDDEN` = the tenant ID,
  subscription ID, storage account name and the employer's domain, comma-separated.
- Environments `dev` (branch `dev` only) and `prod` (branch `main` only, **required reviewer:
  the owner**), each with `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `FABRIC_WORKSPACE_ID` (its own
  workspace), `FABRIC_CONNECTION_ID`, `COST_EXPORT_LOCATION`, `COST_EXPORT_SUBPATH`.
  **No `FABRIC_LAKEHOUSE_ID`** (plan 2's text is stale; the deploy looks it up).
- 🔒 `git push -u origin main`; create and push `dev` from `main`.
- Ruleset on `main`: pull request required, status check **`test`** (workflow `ci`) required,
  no bypass. This is what gates prod on CI.
- **Watch** the first CI run: pytest, the wheel build, Terraform validate, hygiene. Fix
  offline if red. The first `deploy` run on `main` will fail at `publish`'s approval or at
  missing items — expected; record it.

### Step 10: Connect dev to git 👤

- Fine-grained PAT: this repo only, **Contents: Read and write**, expiry 30 days. Store it in the
  owner's password manager only.
- `p9-dev` → Source control → GitHub, branch **`dev`**, Git folder **`fabric`**. Update from Git.
- Check against `fabric/VERIFY.md`, rows 1, 2, 5, 6: the lakehouse is created **schema-enabled**
  (decide now; it can't be migrated), the notebooks bind to `lh_portfolio`, the pipeline resolves
  its notebooks, and shortcut tracking is off. Record every difference and fix it offline on a
  branch (Fabric's own commit shape wins where it differs).
- The sync also creates project 10's six real-time items (dev has no R13 gate). If it fails on
  `es_gateway` or `act_gateway`, follow `fabric/VERIFY.md`'s project 10 dev-sync row.
- Push any change to `dev` → the dev deploy job uploads the wheel and creates the shortcut.

### Step 11: The private extracts 🔒 (Claude, after a yes)

- Regenerate `private/p1_outcomes.jsonl` and `private/p2_results.jsonl` (plan 1 Task 2 CLI) and
  upload both to `Files/bronze/private/` in `p9-dev` (OneLake, Entra auth as the Fabric user).
  Same for `p9-prod` after step 14.

---

## Phase E — Running it

### Step 12: First dev run (Claude triggers, owner watches)

- Run `pl_daily_load` in `p9-dev` with `load_date` = today (UTC).
- Check, in order (each a VERIFY or review item): `%pip` from `/lakehouse/default/Files/wheels`
  works; `load_date` survives the restart; the bronze files land; the cost export is read through
  the shortcut (`spark._jvm`/`_jsc` work); Delta paths under `Tables/silver|gold|reject` show as
  schemas and tables in the explorer and the SQL endpoint (fallback: `CREATE SCHEMA IF NOT
  EXISTS` before the writes); all four checks pass; the published figures match.
- Run it again for the same date → `rerun_unchanged` passes against the first run.
- Every failure is recorded before it's fixed. Fixes go offline (TDD) on a branch, then PR.

### Step 13: The checks failing on purpose (dev only)

One at a time, each recorded with the failed run's ID and error, then restored:
1. a result before any deployment version (append a synthetic row to `Files/bronze/private`
   copy) → `deployment_resolution` fails;
2. a cost row removed from a copied export run → `cost_totals` fails on the manifest count;
3. a published figure seed changed on a dev-only branch → `published_figures` fails;
4. gold altered between two runs of one date → `rerun_unchanged` fails.

### Step 14: Prod 🔒

- PR `dev` → `main`; CI green; merge; the `prod` deploy waits for the owner's approval.
- 🔒 Approve. Check `publish` created the items, the wheel and the shortcut are in place, and
  the PROD schedule shows **enabled** at 06:00 Brisbane. If `startDateTime` (2026-11-01) has
  passed, move it forward first (offline change, PR).
- Upload the private extracts to `p9-prod` (step 11). Run prod once for today; then the backfill
  for each earlier date the bronze needs is a single run with that `load_date`
  (`allow_earlier` is not exposed in the pipeline; backfill dates run oldest first).

---

## Phase F — Measure and publish

### Step 15: Capacity

After at least three scheduled prod runs: from the Capacity Metrics app, CU-seconds per item for
one full load and one backfill, the peak, and throttling if any. Record the trial SKU. Work out
the smallest paid SKU that would fit, without buying one.

### Step 16: The three answers

Query gold in prod (`lakehouse.answers`) with the run date recorded:
- cost per project (untagged and shared shown), in the billing currency;
- quality across models (and "across versions: the data can't tell");
- cost per measured result, estimated beside actual, never blended.

### Step 17: README, report and the push 🔒

- `README.md` in ReleaseLens's style: architecture, layers and grains (`GRAINS`), sources and
  their provenance, what it costs, the three dated answers, capacity, limits, and what waits for
  projects 10 and 11. Every number measured and dated, with sample sizes.
- `docs/live-report.md`: the dated record of steps 3–16, failures included, no identifiers.
- Hygiene check clean; 🔒 PR and merge.

### Step 18: Close

- Tracker: project 9 done, with the commit, the answers and the costs.
- Expire or revoke the PAT if no longer needed; nothing else is left to tear down (the trial
  stops by itself; storage costs cents).
- Note what waits for project 10 (the Eventhouse) and 11 (the semantic model and report).
