# Project 10, plan 2: the live session runbook

> **For the session running this:** a runbook, not a build plan, apart from one offline task
> (step 2, TDD). Execute it inline (superpowers:executing-plans), one step at a time. **Every step
> marked 🔒 waits for the owner's explicit yes in chat for that step**, and a yes for one step never
> carries to the next. Steps marked 👤 are done by the owner in a browser. Claude never enters
> passwords, creates accounts, accepts terms or creates tokens.

**Goal:** Run project 10's one measured live session and publish its four pieces of dated evidence:
1. an Activator alert that fired for a forced failover;
2. the gateway→Eventhouse delay (p50, p95, max, n);
3. a dashboard snapshot;
4. cost per gateway call in project 9's lakehouse.

Then tear down everything that bills by the hour.

**Spec:** `docs/superpowers/specs/2026-10-23-project-10-realtime-gateway-design.md` (§4.5 is the
session; §1 the evidence; §2 the budget). **Built by:** plan 1, merged as PR #4 (`94b93df`).
**Format checks:** `fabric/VERIFY.md`, "Project 10, plan 2". **ReleaseLens:** branch
`p10/eventhub-logging` (`92ef4d2`, unpushed) and its `docs/runbook-gateway.md`, whose steps 2, 3,
5 (test 1 and B1 only) and 8 this runbook calls by number and does not repeat.

## Rules for the whole runbook

- **Project 9's rules apply unchanged.** They are in `docs/superpowers/plans/2026-10-23-plan-3-live-runbook.md`,
  "Rules for the whole runbook":
  - the two sign-ins (personal Azure; `fabric@` in Fabric; never the work account);
  - the Norton CA bundle per process;
  - identifiers only in git-ignored tfvars, GitHub secrets or `private/live/run-log.md`;
  - record everything, failures included;
  - a tracker line after each phase.

  ReleaseLens steps also follow that runbook's own rules: `acctok` must print `True`, nothing that
  names the gateway goes on screen, and Terraform runs only in WSL.
- **Run log:** project 10 appends to `private/live/p10-run-log.md`, giving the UTC time, what ran and
  the outcome. Evidence files go to `private/live/p10/` until step 20 checks them for identifiers.
- **Clock:** the session (steps 11–16) runs between 00:30 and 12:30 UTC (10:30–22:30 Brisbane).
  - It never crosses 00:00 UTC, when the gateway's daily budget and the cost day both roll over.
  - It never meets ReleaseLens's nightly destroy at 14:00 UTC.
- **Money:** API Management bills from step 11 until step 17; on any stop between them, go
  straight to step 17. Event Hubs bills from step 5 until step 19, never longer than the session
  week. On an F2 instead of the trial, the capacity bills while running; pause it whenever this
  runbook says the Fabric side is idle.
- **Replay never feeds a published figure.** Replay events carry `session = "replay"`, and every
  published query, tile, rule, silver load and answer excludes them (plan 1, tested). The replay
  test in step 9 reads the raw table only.

## Preconditions

- Project 9's plan 3 has run through step 14:
  - prod deploys from `main` with the owner's approval;
  - the daily pipeline loads costs;
  - `lh_portfolio` exists in prod.
- The Fabric capacity is on: the trial, or an F2 resumed for the session.
- ReleaseLens's bootstrap is applied: it holds `id-releaselens-gateway` and the model deployments.

---

## Phase A — Before the session week (offline, then two PRs)

### Step 1: Read-only preflight (Claude)

- `az account show` (personal user only), `az consumption budget list` (the A$10 alert exists).
- `az group exists` for `rg-p10-event-hubs` and `rg-releaselens-gateway`: both must be `false`.
- In Fabric (owner's screen or the REST API as `fabric@`):
  - prod has **none** of the six real-time items yet: the R13 gate was off at every prod deploy;
  - dev has them from git sync, idle: `es_gateway` has no connection and stays stopped, and
    `act_gateway`'s rules are off. Dev stays that way for the whole session (spec §4.3: one
    live path, prod).
- Both repos: authors are personal-only, and `main` is clean.
- **Stop** if any gateway or Event Hubs group exists, or if the work account is signed in.

### Step 2: Set the token-spike threshold from measured traffic (offline, TDD)

The spec leaves the threshold to the session plan, "below the gateway's 10,000 per-minute limit".
Plan 1 used 5,000. The 9 October 2026 gateway report (ReleaseLens `docs/gateway-report.md`) puts the
two staged loads at:
- **Test 1, the failover:** 45 calls, one every 4 seconds, so about 15 calls a minute. At about 350
  tokens a call (the burst's ratio), that is about 5,250 tokens a minute. 5,000 would fire the
  spike rule during the failover and confuse the two alerts.
- **B1, the burst:** 60 calls at once, about 21,000 tokens.

**The threshold becomes 8,000 tokens per caller per minute.** That is above test 1's rate with a
margin, below the 10,000 limit, and far below the burst.

**Files:**
- `fabric/kql/queries/token_spike_minutes.kql` (`let threshold = 8000;`)
- `fabric/act_gateway.Reflex/ReflexEntities.json` (the token-spike rule's value)
- the tile and queryset copies of `token_spike_minutes` in `rtd_gateway` and `kq_gateway`
- `fabric/kql/PUBLISHED.md`
- the `act_gateway` conditions row in `fabric/VERIFY.md`
- `tests/test_kql.py:157`
- `tests/test_fabric_items.py:549`

- [ ] **Failing tests** (the new one goes in `tests/test_fabric_items.py`, beside the Reflex
  threshold test). Change the asserted values to `8000`:
  - `test_token_spike_threshold_is_a_let_at_the_top` asserts `"let threshold = 8000;"`;
  - the Reflex test asserts `threshold == 8000`.

  Add one test that pins the reasoning in the threshold's own terms:

  ```python
  def test_the_spike_threshold_sits_between_the_failover_rate_and_the_minute_limit():
      threshold = int(re.match(r"let threshold = (\d+);", _query_text("token_spike_minutes")).group(1))
      failover_tokens_per_minute = 15 * 350  # test 1: one call every 4 s, ~350 tokens a call (9 Oct 2026)
      assert failover_tokens_per_minute * 1.5 <= threshold < 10_000
  ```
- [ ] Run them (WSL, `tests/test_kql.py tests/test_fabric_items.py`) and see them FAIL.
- [ ] Change the values in the files above. The tile, queryset and Reflex copies follow the `.kql`
  file, and the existing copy tests enforce that.
- [ ] Run them again and see them PASS. Then run the hygiene check.
- [ ] Commit `Set the token-spike threshold to 8,000 from the 9 October traffic`, open the PR, and
  **do not merge yet**. Its merge is the prod deploy in step 7.

### Step 3: The ReleaseLens PR 🔒

- Push `p10/eventhub-logging`, open its PR, and merge it when its CI is green. Nothing changes
  live: the switch defaults to off, and the rendered policies equal today's apart from the marker.
- **Stop** if its CI fails.

### Step 4: Pick the session day 👤

- A weekday inside the capacity's life. Then:
  - **Session name:** `p10-YYYY-MM-DD`.
  - **Event Hubs and the Fabric connection** (steps 5–9): the day before.
  - **Cost answer** (step 18): the day after.
- Record the dates in the run log and in the tracker.

---

## Phase B — The day before: Event Hubs, the connection, prod, the replay test

### Step 5: Apply Event Hubs 🔒 (Claude, WSL)

- Write `infra/event-hubs/terraform.tfvars`, which is git-ignored, without printing values:
  - `subscription_id`;
  - `namespace_name`, a new globally unique name;
  - `location = "australiaeast"`;
  - `sender_principal_id` = `az identity show -n id-releaselens-gateway -g rg-releaselens-bootstrap --query principalId -o tsv`;
  - `receiver_principal_ids` = `[ <prod workspace identity> ]` from `infra/fabric`'s output
    `prod_workspace_identity_service_principal_id`.
- `TF_DATA_DIR=$HOME/tfdata/event-hubs terraform init`, then `plan -out=tfplan`. Show the summary:
  - one resource group, the namespace, the hub, the consumer group and two role assignments;
  - local auth off; public network access on.
- `apply tfplan` on the owner's yes. **Billing starts:** record the UTC time.
- Add the namespace name to the repo secret `HYGIENE_FORBIDDEN` (spec §4.3). A secret can't be
  read back, so rebuild the whole value from the forbidden values recorded in the private run logs,
  add the namespace, and pipe it in through stdin. Step 2's PR then runs CI, which shows hygiene
  still passes.
- **Stop** if the plan shows anything else, or the apply fails. On a failed apply, destroy what was
  created.

### Step 6: The Fabric connection 👤, then its secret 🔒

- In Fabric, as `fabric@`: Settings → Manage connections and gateways → New, cloud,
  **Azure Event Hubs**:
  - the namespace and `gateway-calls`;
  - authentication: **Workspace identity**, the `p9-prod` workspace's.
- Record the connection's ID in the run log only.
- Claude sets the prod environment secret `P10_EVENTHUB_CONNECTION_ID` from stdin (`gh secret set
  … --env prod`). This turns on the R13 gate for the next prod deploy.
- **Stop** if the connection type offers no workspace-identity sign-in, or rejects it with local auth
  off. Learn's Eventstream page says it needs "permission to get an event hub's access keys". Then
  the owner decides: turn local auth on for the session week (a spec change, recorded), or stop
  project 10 here.

### Step 7: Deploy prod 🔒, then approve 👤

- Merge step 2's PR (squash) once `test` is green. The push to `main` starts `deploy`; the owner
  approves the `prod` job.
- Expected log, in order:
  1. `publish: done`, with the six real-time types in scope (no "skipped" line);
  2. `upload-wheel` and `ensure-shortcut`, as before;
  3. `onelake-availability: ok`;
  4. `ensure-gateway-shortcut: ok`.
- **On a failure**, follow the VERIFY row for it, fix, and re-run the job:
  - `onelake-availability: HTTP 401`: the token audience;
  - `onelake-availability: HTTP 403`: the deploy identity needs database admin. The owner adds it
    in the KQL database's Permissions as `fabric@`, then the job re-runs;
  - `ensure-gateway-shortcut` 4xx: `_delta_log` is not there yet, so re-run.

  A code fix goes through a branch and a PR like any other.

### Step 8: Check the deployed items (Claude reads; owner clicks where only the UI can)

Work through every row of `fabric/VERIFY.md` "Project 10, plan 2" that a deploy can answer, and
record each as **confirmed** or **differs: …**. In particular:
- **KQL database:**
  - the table, the mapping `gateway_calls_json` and `GatewayCalls()` exist;
  - `.show table gateway_calls policy mirroring` shows the policy enabled, at 5 minutes, with
    backfill;
  - `.show table gateway_calls mirroring operations`.
- **Activator:** Get Item Definition on `act_gateway` shows `shouldRun: true` on both rules, and
  both rules show **Running**.
- **Eventstream:** the source names the connection. Reconnect the Eventhouse destination (spec
  §4.3; manual by Microsoft's docs) with the table `gateway_calls` and the mapping
  `gateway_calls_json`. **Activate** the stream.
- **Queryset and dashboard** open against `kql_gateway`; every tile runs, and is empty for now.
- **Lakehouse:** `lh_portfolio` shows `Tables/dbo/gateway_calls`.
- **Activator email 👤:** add an email action to both rules, to the owner's address. Record whether
  Fabric accepts an address outside the tenant (spec §7). If grouping per caller is offered
  ("On each event grouped by"), group the token-spike rule by `caller`.
- Export `act_gateway`'s definition afterwards to `private/live/p10/act_gateway-after.json` for
  step 20.

### Step 9: The replay test, end to end in prod 🔒

In prod, not dev, which changes plan 1's note. Dev's Eventstream has no connection, and wiring
one in the UI would leave uncommitted changes in a git-synced workspace. Prod is also the exact
path the session uses. Replay rows in the prod table are excluded everywhere by design.

- Give the owner's personal account **Azure Event Hubs Data Sender** on the hub only, until the
  namespace is destroyed. `az role assignment create`, scoped to the hub. The destroy in step 19
  removes it.
- `pip install -e ".[replay]"`, then `P10_EVENTHUB_NAMESPACE=… P10_EVENTHUB_NAME=gateway-calls
  python tools/replay_gateway_events.py --count 50`. Both env vars are set in the process only. It
  must print `sent 50 replay events`.
- Within about 2 minutes, in the queryset (raw table, not `GatewayCalls()`):
  - `gateway_calls | where session == "replay" | summarize n = dcount(request_id)` gives `50`;
  - `gateway_calls | where session == "replay" | summarize delay_s = percentiles((ingestion_time() - event_time) / 1s, 50, 95)` is recorded as the replay delay, never published.
  - `GatewayCalls() | where session == "replay" | count` gives `0`. Every tile stays empty, and
    neither Activator rule fires (check after 10 minutes).
- Within about 10 minutes, `lh_portfolio`'s `Tables/dbo/gateway_calls` preview shows the replay
  rows. That is OneLake availability plus the shortcut, end to end.
- **Stop** (the session waits) on any miss. Look at Event Hubs incoming messages, the Eventstream
  data preview and the destination's status, in that order.
- On an F2, leave the stream active overnight, or deactivate it and re-activate before step 13:
  the owner's choice. Record it.

---

## Phase C — The session day (about 90 minutes of API Management)

### Step 10: Pre-checks (Claude)

- The clock window holds (see "Rules"); `acctok` prints `True` in both windows.
- The capacity is on, `es_gateway` is **Active**, and its destination is green.
- Step 9 passed.
- ReleaseLens's `infra/gateway/terraform.tfvars` gains these, written without printing:
  - `event_hub_logging = true`;
  - the namespace;
  - `event_hub_name = "gateway-calls"`;
  - `p10_session = "p10-YYYY-MM-DD"`;
  - `caller_labels_json` with the owner's object ID → `owner` and `id-releaselens-deploy`'s
    principal ID → `deploy`. Read both with `az` into the file, never onto the screen.

### Step 11: Apply the gateway 🔒

- ReleaseLens runbook step 2, as written. Its plan must also show the logger `p10-eventhub`, the
  named values `caller-labels` (secret) and `p10-session`, and both policies with
  `log-to-eventhub`.
- **API Management bills from here:** record the UTC time.
- **Stop → step 17** if the policy save fails. The `catch {}` and `is int` forms are only checked
  when API Management saves the policy (plan 1 note).

### Step 12: Smoke, and the first events 🔒

- ReleaseLens step 3's smoke, with `--out reports/p10-smoke`.
- Within 2 minutes, in the queryset: `GatewayCalls() | where session == "p10-YYYY-MM-DD"` shows one
  row per gateway call. Check the values:
  - `caller` = `owner`;
  - `answered_by` = `primary`;
  - `tokens` not null;
  - no GUID in `api` or `operation`. Record what `api` holds: `context.Api.Name` may be the display
    name.
- **Stop → step 17** if nothing arrives in 5 minutes and the step 9 checks can't find why.

### Step 13: A second caller (optional) 🔒

- If the owner wants a `deploy` row: set the two session secrets and dispatch `gateway-check.yml`,
  as in ReleaseLens step 5's "Before step 6" and step 6 B3. That is one call, as
  `id-releaselens-deploy`. Its event must show `caller` = `deploy`.
- The secrets are deleted in step 17.

### Step 14: The forced failover 🔒

- Record the UTC time, then run ReleaseLens step 5's **test 1** as written: direct, 150 s, then
  gateway. The forcing time is the start of the gateway run.
- In the run log: the before-run's failure count (the test is inconclusive under 14 of 45) and
  whether any record has `region` = `secondary`.
- Watch `act_gateway`'s failover rule. Its query runs every 5 minutes, so expect an activation
  within about 10 minutes of the first `secondary` answer. Record the activation time from its
  history.

### Step 15: The token spike 🔒

- At least 2 minutes after test 1 ends, record the UTC time. Then run ReleaseLens step 5's **B1**
  only (`minute-budget`): a burst of 60. **Never B2**: it spends the day's budget, and the session
  doesn't need it.
- Record the token-spike rule's activation time. The burst may also trip the primary, so a second
  failover activation is possible: record it as such.
- Record each email's outcome (arrived, rejected, or not attempted).

### Step 16: Read the evidence (Claude, owner exports)

- **All published queries**, for this session, from the queryset, saved as CSV in
  `private/live/p10/`:
  - `delay_p50_p95_max` (n, p50, p95, max, in seconds);
  - `refusals_per_minute`, `answered_by_per_minute`, `tokens_per_minute_per_caller`;
  - `session_counts`, `failover_minutes`, `token_spike_minutes`.
- **Eventhouse count:** `GatewayCalls() | where session == "…" | count`. This is step 18's
  `eventhouse_count`.
- **Activator:** each rule's activation history (times), as a screenshot 👤 and as typed times in
  the run log.
- **Dashboard snapshot 👤:** export the image over the session's time range to `private/live/p10/`.
- **Duplicates:** check them with VERIFY's last row (`request_id`s with more than one distinct
  payload).

### Step 17: Destroy the gateway 🔒

- ReleaseLens runbook step 8, as written, with its purge and absence checks. If step 13 ran, also
  delete the two session secrets.
- Record the UTC time and API Management's billed hours.
- **Then** deactivate `es_gateway` and turn off both Activator rules 👤. Nothing more will arrive,
  and both cost capacity. Pause an F2.

---

## Phase D — After the session

### Step 18: The cost answer (the next day) 🔒

- On an F2, resume the capacity first: the shortcut reads the OneLake copy (spec §7).
- After project 9's daily load has run for the session day, check that gold `fact_daily_cost` has
  rows for `rg-releaselens-gateway` that day. If not, wait a day; the export lags.
- Run `nb_gateway_load` in prod 👤 with `session = "p10-YYYY-MM-DD"` and
  `eventhouse_count = <step 16's count>`. The check must pass. Record the printed answer:
  - calls;
  - tokens and tokens per call;
  - API Management cost and cost per call, in AUD.
- **Stop** if the count check fails. The reject rule says: record it, never adjust a count.

### Step 19: End of the session week: teardown 🔒

- `terraform destroy` for `infra/event-hubs`. This also removes step 9's role. Check the group is
  absent and record the hours billed.
- Delete the prod secret `P10_EVENTHUB_CONNECTION_ID`. Later prod deploys then skip the real-time
  items (R13), and nothing republishes the Eventstream against a dead connection.
- The owner deletes the Fabric connection 👤.
- In ReleaseLens's local tfvars, set `event_hub_logging = false`, so any later gateway session
  applies as before.
- The six items stay in prod, stopped, as the CV's working definitions.
- A second session is a new owner decision (spec §7), not part of this runbook.

### Step 20: Publish 🔒

- **Check every evidence file for identifiers** before it leaves `private/`:
  - **Snapshot:** no workspace or item URL, tenant, email or GUID visible; crop or re-export if
    needed.
  - **CSVs:** labels only.
  - Then copy them to `docs/live/p10/`.
- **`docs/live-report-p10.md`:** the four pieces of evidence, each with its date, sample size and
  limits.
  - Forcing time beside activation time, for both rules.
  - The delay p50, p95, max and n.
  - The snapshot.
  - Cost per call.
  - Costs from the cost export (spec §5): API Management hours, Event Hubs days, tokens and capacity.
  - Everything that failed or was inconclusive.
- **README:** a project 10 section.
  - The demo-stream disclaimer.
  - The threshold (8,000) and why.
  - "429s per backend" as what the gateway can observe (spec §4.1).
  - The four answers.
  - The status table.
- **`fabric/VERIFY.md`:** every project 10 row resolved. Where Fabric's written definition differs
  (step 8's export), bring the definition in git into line, with its test, and say so.
- Branch, PR, and merge on the owner's yes. Cancel the deploy run the merge triggers: there is no
  connection now.
- In ReleaseLens, one line in its gateway report pointing to this report. That change goes in its
  own PR, on a yes.

### Step 21: Close

Write the tracker line for project 10 and append to the ledger. Check that nothing project 10
created still bills: `az group exists` for both groups gives `false`, and the capacity is back to
its plan.

---

## Review Focus (what most likely goes wrong live)

1. **The Eventstream can't read with local auth off.** That is step 6's stop, and it is decided
   before anything else bills for long.
2. **An alert doesn't fire in time.** The rules poll every 5 minutes. Steps 14 and 15 wait at least
   10 minutes before calling it a miss, and record a miss as a miss.
3. **Replay or smoke traffic in a published figure.** Replay is excluded by `session`. Smoke calls
   carry the session, so they appear in the figures, and the report says so.
4. **An identifier in the snapshot.** Step 20 checks before anything leaves `private/`.
5. **Something left billing.** Step 17 (API Management), step 19 (Event Hubs), and the stream and
   rules stopped in step 17.
