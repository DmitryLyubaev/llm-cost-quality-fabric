# Project 11, plan 2: the live runbook

> **For the session running this:** a runbook, not a build plan, apart from its offline tasks
> (step 6, step 9 if needed, TDD; step 13, docs). Execute it inline (superpowers:executing-plans), one step at a time.
> **Every step marked 🔒 waits for the owner's explicit yes in chat for that step**, and a yes for
> one step never carries to the next. Steps marked 👤 are done by the owner in a browser. Claude
> never enters passwords, creates accounts or users, assigns roles in Entra, accepts terms or
> creates tokens.

**Goal:** Take project 11 live:
- the semantic model in dev and prod, bound to its fixed identity;
- the report authored in dev and committed;
- the model-versus-gold check passing as the owner and as both test users;
- dated screenshots of the three pages and of the security checks;
- the README section.

All of it is finished by about **20 December 2026**, when the owner's Power BI individual trial
likely lapses.

**Spec:** `docs/superpowers/specs/2026-10-24-project-11-semantic-model-design.md` (§4.7 is this
run). **Built by:** plan 1, merged as PR #7 (`d8e8737`). **Format checks:** `fabric/VERIFY.md`,
"Project 11, plan 2". **Facts:** `docs/research/2026-10-24-semantic-model-facts.md`.

## Rules for the whole runbook

- **Project 9's rules apply unchanged.** They are in `docs/superpowers/plans/2026-10-23-plan-3-live-runbook.md`,
  "Rules for the whole runbook":
  - the two sign-ins: personal Azure, and `fabric@` in Fabric, never the work account;
  - the Norton CA bundle per process;
  - identifiers only in git-ignored files, GitHub secrets or private run logs;
  - record everything;
  - a tracker line after each phase.
- **Run log:** append to `private/live/p11-run-log.md` (UTC time, what ran, outcome). Evidence goes to
  `private/live/p11/` until step 15 checks it.
- **Sign-in names:**
  - **Where they live.** The test users' UPNs live only in `private/access.csv` (git-ignored), in
    OneLake (`Files/private/access/`, `security.user_project`) and in Entra. Never in chat, a
    commit, a screenshot or a log.
  - **How Claude refers to them.** Only as "the p4 lead" and "the p3 lead".
- **Leads never get a workspace role.** They get Read and Build on `sm_portfolio`, and, for the
  screenshots only, view access to the report. RLS does not filter Admin, Member or Contributor,
  and a Viewer can read the lakehouse through the SQL endpoint.
- **Fabric's shape wins.** Where Fabric writes a definition differently from plan 1's hand-written
  one, git takes Fabric's form, and the tests follow it.

## Preconditions

- **Project 9's plan 3 has run through step 14.** That means:
  - dev and prod are on git sync and the deploy;
  - the daily pipeline loads gold;
  - `lh_portfolio` exists in both workspaces.
- **Project 10's session has run,** for the gateway page. If it hasn't, the page is authored on
  empty visuals, and the README says the page waits for the session. The rest of this runbook
  doesn't depend on it.
- **The capacity is on.**

---

## Phase A — Licence, users and the hygiene list

### Step 1: Read-only preflight (Claude, owner's screen where needed)

- **Licence:** the owner opens Account manager as `fabric@` 👤 and reads the Power BI licence state
  and its end date. Record it, and set the working deadline as that date minus 3 days.
- **Fabric:**
  - both workspaces show `sm_portfolio`;
  - dev's copy is unframed, because its source is still the empty GUID (VERIFY, first row);
  - prod has no model yet, because the P11 gate is off.
- **Gold:** both lakehouses list `gold.fact_gateway_call` and `security.user_project`, empty or
  filled. That is the VERIFY `ensure_table` row.
- **Stop** if the licence has already lapsed. Then the owner decides: buy one month of Pro
  (AU$21 + GST), or stop project 11.

### Step 2: The two test users 👤

- **Create the users.** In Entra, in the personal tenant (check the directory), the owner creates
  two member users. Their names must appear nowhere in the repo, so don't use `lead-p4` or
  `lead-p3` (VERIFY's hygiene row). Assign no licence and no roles.
- **Write `private/access.csv`.** The owner, or Claude after a yes 🔒 with the UPNs read from the
  owner's file and never echoed, writes:
  - the header `upn,project`;
  - one lead mapped to `p4`;
  - the other lead mapped to `p3`.
- **Record** only "two users created" in the run log.

### Step 3: The hygiene list 🔒

- **What to add.** Add the personal tenant's domain (and any custom domain) and both users' UPN
  local parts to the repo secret `HYGIENE_FORBIDDEN`.
- **How.** The secret can't be read back, so rebuild the whole value from the private run logs'
  forbidden values, plus these. Pipe it in through stdin.
- **Check.** Step 6's PR runs CI, which shows hygiene still passes.

---

## Phase B — Dev: frame the model, author the report

### Step 4: Point dev's model at dev's lakehouse 🔒

- **Commit the IDs.** On a branch from `dev`, replace the two empty GUIDs in
  `fabric/sm_portfolio.SemanticModel/definition/expressions.tmdl` with dev's workspace ID and dev's
  `lh_portfolio` ID. These are Fabric IDs, which may be committed (owner, 21 Oct). Commit, then
  push to `dev` (the dev branch takes direct commits).
- **Sync and refresh 👤.** The owner runs dev's "Update from Git", then refreshes `sm_portfolio`
  in dev, as the owner.
- **Check that it frames.**
  - The model refreshes, and every table shows rows, or zero rows for the session tables.
  - The relationships on `dim_date[date]` (`dateTime`) are accepted. That is the VERIFY
    `dateTime` keys row.
  - If they are rejected: stop, then run step 6's fallback (an integer `date_key` in gold) before
    going on.
- **Stop** if the refresh errors on the expression shape. Record Fabric's error, then compare with
  the VERIFY expression rows. If needed, take Fabric's own form: create a Direct Lake model on
  `lh_portfolio` in the web UI, read its `expressions.tmdl` from a commit, and copy its shape.

### Step 5: The access table in dev 🔒

- **Upload.** Upload `private/access.csv` to dev's `Files/private/access/` (OneLake, Entra auth as
  `fabric@`).
- **Load 👤.** The owner runs `nb_access_load`. It must print counts only: 2 rows, 2 users,
  2 projects.

### Step 6: Bring the model's files into line with Fabric's (offline TDD, then a PR) 🔒

- **Commit Fabric's shape 👤.** The owner commits from dev's Source control, which writes Fabric's
  own `sm_portfolio` shape. Then pull `dev` locally.
- **Diff and update tests.** Diff `fabric/sm_portfolio.SemanticModel/` against plan 1's version.
  For each VERIFY row the diff answers, update `tests/test_semantic_model.py` to Fabric's form:
  - `lineageTag` GUIDs go into the identifier test's allow-list;
  - Fabric's relationship names;
  - the expression name;
  - `compatibilityLevel`;
  - the culture file.

  Write the failing assertion first, then take Fabric's file.
- **Fallback.** Only if step 4 needed it, add `date_key` (an int, `yyyyMMdd`) to gold's dates and
  `dim_date`, and relate on it, with tests in `tests/test_gold.py` and
  `tests/test_semantic_model.py`.
- **Run tests.** Run `tests/test_semantic_model.py tests/test_fabric_items.py tests/test_hygiene.py`
  (plus `tests/test_gold.py` if the fallback ran). Resolve each VERIFY row the diff answered.
- **PR to `main`.** Don't merge yet: its merge is the prod deploy in step 8.

### Step 7: Author the report in dev 👤 (owner builds; Claude guides)

- **Build it.**
  - In dev, create the report `rp_portfolio` on `sm_portfolio` (web editor) with the three pages
    from spec §4.3 and the agreed wireframe. Use only the model's measures, with no report-level
    measures that the check can't see.
  - **Gateway page:** on dev's empty gateway table the visuals are blank. Lay them out, and check
    them in prod in step 12. If laying out on blank visuals is impractical, copy prod's
    `gold.fact_gateway_call` into dev once (no identifiers in it), then reload.
- **Commit it.** Commit from Source control, and pull `dev`. Then check:
  - `rp_portfolio.Report/definition.pbir` references the model `byPath` (`../sm_portfolio.SemanticModel`).
    If Fabric wrote `byConnection`, change it to `byPath` on the branch, because fabric-cicd
    rewrites only `byPath`;
  - the report files hold no UPN, tenant or URL (the hygiene check).
- **Add tests to step 6's branch.** Add an item test for `rp_portfolio`: `.platform` type
  `Report`, and `byPath` to `sm_portfolio`. Push it to step 6's PR.

---

## Phase C — Prod: connection, deploy, access, check

### Step 8: The fixed-identity connection 👤, its secret 🔒, then deploy 🔒👤

- **Create the connection.** In Fabric as `fabric@`, under "Manage connections and gateways", the
  owner creates a cloud connection for OneLake / `lh_portfolio` with authentication **Workspace
  identity** (prod's), and single sign-on off.
  - Give the deploy service principal **User** on the connection. fabric-cicd skips binding a
    connection that the deploy identity can't see.
  - Give prod's workspace identity read access to `lh_portfolio`, if Fabric doesn't grant it
    already.
  - Record the connection's ID in the run log only.
- **Set the secret.** Claude sets the prod environment secret `P11_MODEL_CONNECTION_ID` from stdin.
- **Deploy.** Merge step 6's PR once `test` is green. The push to `main` starts `deploy`, and the
  owner approves `prod`. Expected log, in order:
  1. `publish: done`, with no "model and report skipped" line, and IDs shown as `<id>`;
  2. the project 9 and project 10 steps as before;
  3. `refresh-model: ok`.
- **On a failure**, follow the matching VERIFY row, fix, and re-run the job:
  - **`refresh-model: the model is not bound to its connection`:** the owner binds
    `sm_portfolio` to the connection in the model's settings (Gateway and cloud connections) as
    the owner. That is ruling R7.
  - **HTTP 401 or 403 on the refresh:** the tenant setting for service principals and Power BI
    APIs, or the token audience.
  - **HTTP 400:** a refresh was already running, so re-run.

  A code fix goes through a branch and a PR.

### Step 9: How the model refreshes after the daily load (decide, then maybe one offline task)

- **Check automatic updates.** After the next daily load in prod, check whether the model shows
  the new data without `refresh-model`. "Keep your Direct Lake data up to date" must be on. It's
  read-only to the owner, because the service principal owns the model.
- **If it shows the new data:** record "automatic updates" in VERIFY, and the decision is made.
- **If it doesn't:** add a refresh after `nb_load` in `pl_daily_load`, run as a Web activity or a
  small notebook step under the pipeline's identity. Do it as offline TDD on a branch: a pipeline
  item test, plus the step. Open a PR and merge it on a yes.
- **Either way,** the check (step 11) runs only after the load and the refresh have finished.

### Step 10: Access 🔒 and the leads' permissions 👤

- **Load the access table.** Upload `private/access.csv` to prod's `Files/private/access/`, then
  the owner runs `nb_access_load` 👤. It prints 2 / 2 / 2.
- **Share the model 👤.** The owner shares `sm_portfolio` with each lead with **Read** and
  **Build** (item permissions, Share). No workspace role.
- **Check.** The workspace's Manage access lists neither lead. The model's permissions list both.

### Step 11: The model-versus-gold check 👤

- **Run it.** The owner runs `nb_model_check` in prod, signed in, with
  `sessions = "p10-YYYY-MM-DD"` (project 10's session), or `""` if it hasn't run.
- **Expected output:** `passed`, with the detail naming:
  - the answers compared;
  - the gateway sessions;
  - both leads by their projects (p4 and p3), each with at least one row compared.
- **Record the printed detail.** It holds no UPN.
- **On a failure:** read the mismatch (measure, key, both values) and follow the matching VERIFY
  row:
  - a duplicate key on the quality grain;
  - the tolerance;
  - `_dax_rows` column names.

  Fix through a branch and a PR, and re-run. Never adjust a gold figure to match.

### Step 12: Screenshots 👤 (owner takes them; Claude checks them)

- **The three pages as the owner.** Capture each page over the full date range. Then capture the
  gateway page over the session.
- **"Test as role" → Now viewing as each lead.** For each lead, capture pages 1–3:
  - the p4 lead: p4's spend, an empty quality page, and the whole gateway page;
  - the p3 lead: p3's spend and study, and an empty gateway page.

  Note on the screenshots that `dim_model_deployment`'s names are visible to leads (VERIFY: an
  accepted gap).
- **Check every image before it leaves `private/live/p11/`.** There must be:
  - no UPN: the "viewing as" banner shows it, so crop or redact it;
  - no tenant;
  - no workspace or item URL;
  - no GUID.

  Name each file by page and viewer label (`owner`, `p4-lead`, `p3-lead`).
- **Capacity Metrics:** record the model's and report's CU for the session's days (spec §5).

---

## Phase D — Publish and close

### Step 13: Resolve VERIFY and update the README (offline, then a PR) 🔒

- **VERIFY.** Resolve every "Project 11, plan 2" VERIFY row as confirmed (with date) or
  changed (with what changed). Remove rows that are fully confirmed and fold their fact into the
  README or code comments.
- **README: a project 11 section.** It covers:
  - the three pages as dated screenshots (`docs/live/p11/`);
  - RLS shown three ways: owner, the p4 lead, the p3 lead;
  - the check's printed result, with its date;
  - the measures, each mirroring its gold answer;
  - "Direct Lake fallback: know, don't build", as a note;
  - the licence and its deadline;
  - every limit: the AUD rate under RLS, `dim_model_deployment` visible to leads, the 0.005
    tolerance, and the gateway page's single demo session.
- **README tables.** Update the status table, the answers table and the repo layout.
- **`docs/live-report-p11.md`:** the run as it happened (dates, every step's outcome, failures
  and fixes), built from the run log with identifiers removed, as in projects 9 and 10.
- **Ship it.** Branch, PR, and merge on a yes. Cancel the deploy run, or approve it if the owner
  wants the docs deployed (nothing in `fabric/` changed).

### Step 14: Licence after the deadline (decide) 👤

- If any authoring remains when the individual trial ends, the owner buys one month of Pro or
  stops there.
- Viewing the screenshots needs no licence; the README is the evidence.

### Step 15: Close

- **Tracker:** add a line for project 11 and mark it **done**. Close phase 4's core projects if 9
  and 10 are also done.
- **Ledger:** append a closing line to the ledger.
- **The test users** stay until the owner decides otherwise. They cost nothing. Deleting them
  later breaks only "Test as role" for those names, not the screenshots.
- **The fixed-identity connection** stays: prod's model needs it.

---

## Review Focus (what most likely goes wrong live)

1. **A lead who can read gold past the filter,** through a workspace role or a binding that never
   applied. Step 10 grants model-only access, `refresh-model` fails closed when the binding is
   missing (step 8), and the screenshots show the filter (step 12).
2. **A UPN in a screenshot, commit or log.** The "viewing as" banner shows it. Step 12 checks every
   image, and the hygiene list (step 3) catches the repo.
3. **The model not framing in dev,** because of the expression shape or `dateTime` keys. Step 4
   stops early, with Fabric's own form and the `date_key` fallback ready.
4. **Stale figures in the report or the check after a load.** Step 9 settles how the model
   refreshes before step 11 runs.
5. **Running out of licence mid-authoring.** Step 1 sets the deadline, and step 14 decides
   whether to pay.
