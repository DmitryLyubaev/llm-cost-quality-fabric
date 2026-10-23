# Project 10: Real-Time Intelligence over the AI gateway — design

Written 23 October 2026 · Phase 4, project 10 of 11 · Status: draft for owner approval

Phase plan: `portfolio-plan/plan/phase-4.md` section 10. Builds on project 9
(`docs/superpowers/specs/2026-10-21-fabric-lakehouse-design.md`, this repo) and the ReleaseLens
API Management gateway (project 4). Recon behind this design, 23 October 2026: the gateway's
Terraform and policies in ReleaseLens, and Microsoft Learn on API Management, Event Hubs and Fabric
Real-Time Intelligence (sources cited in `docs/research/2026-10-23-realtime-facts.md`, added by the
plan's first task).

## 1. Intent

A CV piece for senior and AI-engineer roles. It shows Fabric's real-time side done properly on a real
AI gateway, measured, rather than every feature switched on.

The ReleaseLens gateway exists only during a live session; the last ones ran on 9–14 October 2026.
So the project is **one measured live session**: the gateway is stood up once for about an hour,
real traffic is driven through it by the existing test harness, two things are staged on purpose (a
failover to the backup region and a token spike from one caller), and four pieces of dated evidence
come out of it:

1. **An alert that actually fired** for the staged failover, with its timestamp beside the moment
   the failover was forced.
2. **A measured end-to-end delay**: seconds from a gateway call to that event being queryable in the
   Eventhouse (p50, p95, max, with the sample size).
3. **A dashboard snapshot** of the session: throttling, calls answered by the backup region, and tokens
   per minute per caller.
4. **The same events in project 9's lakehouse**, joined to its cost model: cost per gateway call for
   the session's day.

Then everything that bills by the hour is torn down. The README says plainly that this is a demo
stream that arrives in bursts, not production traffic.

### Goals

- The four pieces of evidence above, each dated, with sample sizes and limits.
- The full Fabric real-time path as code in this repo: Eventstream, Eventhouse with a KQL database,
  KQL queryset, Real-Time dashboard, Activator, and OneLake availability shortcut into the project 9
  lakehouse. Deployed the same way as project 9 (dev by git sync, prod by `fabric-cicd` behind approval).
- Keyless end to end: API Management sends with its managed identity; Fabric reads with the
  workspace identity.

### Non-goals

- Anything that pretends to run continuously. No synthetic traffic in the published evidence.
- Alerts on conditions that never happen naturally, beyond the two staged ones.
- Backfilling earlier sessions (no event was ever sent to Event Hubs; App Insights history is not replayed).
- Project 11's semantic model and report.

### Anti-goals: a failure even if it works

- A published figure that came from the replay script instead of the real gateway.
- Raw Entra object IDs, tenant or subscription IDs, client IPs, or endpoint URLs in the repo, a
  dashboard snapshot, or a public report.
- Anything left billing by the hour after the session (API Management, the Event Hubs namespace,
  a paid capacity left running).

## 2. Constraints

- Project 9's constraints hold unchanged: identity rules (personal tenant; the `fabric@` user in
  Fabric; never the work account), no identifiers in public files, commits as
  `Dmitry Lyubaev <Dmitry.Lyubaev@gmail.com>`, the owner's yes before every outward step, Terraform
  runs in WSL.
- **Order:** after project 9's live work, on the same Fabric capacity (the trial from 8 November
  2026, or the paid-F2 fallback paused between sessions).
- **Budget** (A$10 a month alert): API Management Basic v2 about US$0.21 an hour (one ~1-hour
  session); Event Hubs **Standard** A$0.0427 an hour, so the namespace lives only for the session
  week (about A$7), never standing; model tokens under US$0.10 a session; Fabric capacity free on
  the trial, else about A$0.60 an hour while running.
- **Two repos.** The gateway policy and the API Management logger change in ReleaseLens
  (`infra/gateway`), by its own PR and its own runbook rules. Everything else is in this repo.

## 3. Approaches

| | What | For | Against |
|---|---|---|---|
| **A (chosen)** | Gateway → Event Hubs → **Eventstream** → **Eventhouse** (KQL) → Real-Time dashboard; **Activator** on a dashboard query; OneLake availability → shortcut into project 9's lakehouse | Shows the whole of Fabric Real-Time Intelligence; all items have git, `fabric-cicd` and Terraform support | Most moving parts; Eventstream uses about 1 CU while events flow; an Eventstream with an Eventhouse destination needs reconnecting after a deploy |
| B | Same without Eventstream: the Eventhouse reads Event Hubs through its own data connection | Fewer parts, less capacity, nothing to reconnect | Eventstream never appears on the CV |
| C | A script pushes synthetic events into Event Hubs; no gateway session | Cheapest, simplest | The alert and the delay would not be measured on real traffic, which breaks the point |

The owner chose **A** on 23 October 2026. One piece of C is kept **for development only**: a small,
clearly labelled replay script to test the Fabric side cheaply before the real session. It never
feeds published evidence.

## 4. Design (decided)

### 4.1 The event: one per gateway call

Emitted by `log-to-eventhub` in the gateway policy (ReleaseLens `infra/gateway/policies/api-v1.xml`,
and rev 2), as one JSON object:

| Field | Source in the policy |
|---|---|
| `event_time` | `context.Timestamp` (UTC) |
| `request_id` | `context.RequestId` |
| `session` | a named value set per session (for example `p10-2026-11-12`) |
| `api`, `operation` | `context.Api.Name`, `context.Operation.Id` |
| `caller` | a **label**, not the object ID: a named value maps the 2–3 known callers' `oid`s to `owner`, `deploy`, `webapp`; anything else is `other`. Raw `oid`s never leave the gateway |
| `answered_by` | `primary` or `secondary` (the existing backend label), null when no backend answered |
| `status` | final status code |
| `refused_by_gateway` | true for the gateway's own 401/403/429 (logged from `on-error`) |
| `tokens` | `llm-token-limit`'s `tokens-consumed-variable-name` (added to the existing policy) |
| `latency_ms` | `context.Elapsed` |

No client IP, no request or response body. There is no deployment or model field, by design: the
gateway calls one chat deployment per region, so `answered_by` already says which one answered, and
adding a model field would mean reading the request body in the policy. *(My call.)* The same statement runs at the end of `outbound` and
in `on-error`, so refused calls are counted too. "429s per backend" is shown as what the gateway can
actually observe: its own refusals, and calls answered by the backup region (the failover retry hides
the primary's 429 inside one request). The README says so.

### 4.2 Event Hubs

- **Standard** tier namespace, one hub, one partition, 1-day retention, a dedicated consumer group for
  Fabric. Standard rather than Basic: Basic's single consumer group with Eventstream is unconfirmed
  in Microsoft's docs.
- Public network access, Entra-only (local auth off), like the cost-export storage (project 9
  amendment 6).
- API Management's existing user-assigned identity gets **Azure Event Hubs Data Sender** on the hub;
  the Fabric workspace identity gets **Azure Event Hubs Data Receiver**.
- Its own Terraform stack in this repo (`infra/event-hubs/`), applied at the start of the session
  week and destroyed at its end.
- **ReleaseLens stays usable without it.** The Event Hubs logger and the two `log-to-eventhub`
  statements are switched by a gateway-stack variable (default off), rendering the policy from a
  template, so every other gateway session (projects 4–6, the MCP API) applies exactly as before.
  *(My call.)*
- **Wiring across the two repos** is by git-ignored `terraform.tfvars` values the owner copies
  between the stacks: the gateway identity's principal ID into `infra/event-hubs` (which creates the
  Sender role on the hub; the gateway stack still grants no roles), and the namespace and hub names
  into the gateway stack. No remote-state reads across repos. *(My call.)*

### 4.3 Fabric items (in `fabric/`, deployed like project 9's)

**One live path.** The definitions exist in both workspaces (dev by git sync, prod by `fabric-cicd`),
but only **prod** connects to Event Hubs and runs the session. In dev the Eventstream source stays
stopped and the Activator rules off, except while running the replay script; dev and prod are never
live at once, so an F2 never carries two Eventstreams and no alert fires twice. The published
evidence comes from prod. *(My call.)*

**Identifiers stay out of git**, as in project 9: the Event Hubs namespace and hub names in the
Eventstream definition, and the Eventhouse query URI in the queryset and dashboard, are injected at
deploy time (`fabric-cicd` `$ENV:` replacements from GitHub environment secrets, and its
`$items.Eventhouse.eh_gateway.$queryserviceuri` token). The CI hygiene check gains the namespace name.
*(My call: the strict reading of project 9's rule.)*

- **Eventstream** `es_gateway`: source Event Hubs (workspace identity), destination Eventhouse table
  `gateway_calls`. Reconnecting the destination after each deploy is a documented runbook step.
- **Eventhouse** `eh_gateway` with KQL database `kql_gateway`; table `gateway_calls` with the 4.1
  schema plus `ingestion_time()`. Never in Capacity Planner mode (its minimum is the whole of an F2).
  **OneLake availability on**, target latency 5 minutes, set by a KQL management command in the
  deploy (no Terraform resource sets it).
- **KQL queryset** `kq_gateway`: the queries behind every tile and every published figure, in git.
- **Real-Time dashboard** `rtd_gateway`: throttling (gateway refusals by status per minute), calls by
  `answered_by` per minute, tokens per minute per caller, end-to-end delay distribution.
- **Activator** `act_gateway`, two rules on Stat- or Column-type dashboard tiles with a fixed time
  range: (1) any call answered by `secondary` in a minute; (2) one caller's tokens in a minute above
  a threshold set from the session plan (below the gateway's 10,000 per-minute limit). Its activation
  history is the evidence; email delivery is attempted and its outcome recorded either way.
- **Shortcut** from the Eventhouse's OneLake copy into the project 9 lakehouse (`Files` or `Tables`
  per project 9's layout), created by the deploy like the cost shortcut.

### 4.4 Into project 9's model

- Coded against project 9 as merged on `main` (its package, paths and seeds are built; only its live
  run waits for Fabric).
- **Its own notebook, run after the session**, not part of project 9's daily pipeline: a one-off
  session dataset must not change what project 9's re-run and published-figure checks guard.
  *(My call.)*
- New silver table `gateway_calls` (MERGE on `request_id`) and gold `fact_gateway_call`
  (grain: one gateway call), joined to `dim_date` and `dim_project` (p4, the gateway's project). No
  join to the deployment dimension (see 4.1).
- Project 9's seeds gain `p10` in `dim_project` and an attribution rule for the Event Hubs resource
  group, so its cost is attributed rather than untagged.
- New answer: **cost per gateway call for the session's day** = the API Management resource group's
  attributed cost that day (project 9 cost export) / calls that day, beside the token estimate. The
  cost export lags up to a day, so this answer is computed the day after the session.
- New quality check: every gold `fact_gateway_call` row's `request_id` is unique, and the session's
  call count in gold equals the count in the Eventhouse.

### 4.5 The measured session (runbook in plan 3 of project 10)

1. Pre-checks: Fabric capacity on; Eventstream running (it stops when a capacity pauses); a replay
   test passes end to end.
2. Apply ReleaseLens `infra/gateway` (owner's yes); set the session named value.
3. Baseline traffic with the existing harness (smoke, then a mix of owner and deploy calls).
4. **Forced failover** with project 4's method (runbook-gateway test 1). Record the forcing time.
5. **Token spike**: one caller sends a timed burst past the Activator threshold. Record the time.
6. Read the evidence: Activator activation times, KQL delay figures, dashboard snapshot (exported
   image, identifiers checked), lakehouse row counts.
7. Destroy the gateway (purge and absence checks per its runbook); destroy `infra/event-hubs` at the
   end of the week; pause or leave the Fabric capacity per its plan.
8. Next day: the cost-per-call answer from the cost export.

### 4.6 Tests (offline first)

- ReleaseLens: policy XML tests for the new logger statements (both sections, no IP, label mapping,
  `tokens-consumed-variable-name` present), in its existing infra test suite.
- This repo: pytest for the silver/gold transforms and the new check; item-definition tests like
  project 9's; KQL text checks (every published figure has a query in the queryset); Terraform
  fmt/validate for `infra/event-hubs`.
- The replay script, labelled development-only, with a test that its events carry `session = "replay"`
  and are excluded from every published query.

## 5. What it costs

Recorded after the session, with the date: API Management hours, Event Hubs namespace days, tokens,
Fabric capacity (trial or F2 hours), all from the cost export where possible.

## 6. Left to the builder

Exact KQL, tile layout, item names beyond the ones above, the Activator threshold value (set from the
session plan and stated in the README), and the replay script's shape.

## 7. Risks and unknowns

- **Fabric capacity** is the same dependency as project 9 (trial from 8 November, else F2). The
  trial's size (F4 or F64) is unknown until it starts; on an F2 or F4, the session runs with nothing
  else on the capacity.
- **Activator email** may only reach mailboxes in the tenant, and the `fabric@` user has none: the
  activation history is the primary evidence, email the secondary.
- **OneLake availability while a capacity is paused** is unverified; the lakehouse join is done while
  the capacity is on.
- **Eventstream reconnect after deploy** is manual by Microsoft's own docs.
- **The pinned `azurerm` in ReleaseLens** may not have the Event Hubs logger block with managed
  identity; fall back to `azapi` (as ReleaseLens already does for backends).
- **A second session** may be needed if the first misses evidence; it is a new owner decision, not
  assumed.
