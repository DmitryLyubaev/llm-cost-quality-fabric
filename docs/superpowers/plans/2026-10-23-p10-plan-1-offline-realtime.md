# Project 10, plan 1: real-time path, offline — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and test, with no Azure, Fabric or GitHub calls, everything project 10's one
measured live session needs: the gateway event contract, the ReleaseLens policy change (switchable,
default off), the Event Hubs Terraform, the KQL, the six Fabric real-time items, the deploy-script
additions, the lakehouse transforms and answer, and a development-only replay script.

**Architecture:** One event contract (`contracts/gateway-event.schema.json`) is the source of truth
for the policy that emits events, the KQL table that stores them, and the silver transform that reads
them. The Fabric items live in `fabric/` and deploy like project 9's. The lakehouse side is a new
`lakehouse.gateway` module, run by its own notebook after the session, never by the daily pipeline.

**Tech Stack:** project 9's stack; API Management policy XML (ReleaseLens); Terraform `azurerm`
for Event Hubs; KQL; Fabric item definitions (Eventhouse, KQL database, KQL queryset, KQL dashboard,
Reflex, Eventstream); `azure-eventhub` for the replay script.

**Spec:** `docs/superpowers/specs/2026-10-23-project-10-realtime-gateway-design.md` (approved 23 Oct
2026). Recon: a local file the controller provides, copied in by Task 1.

**Plan 2** (written after this merges) is the live session runbook.

## Global Constraints

- Project 9's rules hold: commits as `Dmitry Lyubaev <Dmitry.Lyubaev@gmail.com>`; no push without the
  owner's yes (branches + PRs; `main` is protected); no Azure, Fabric or GitHub calls in this plan;
  Terraform `fmt`/`init -backend=false`/`validate` only, in WSL.
- No identifiers in committed files: no Entra object IDs, tenant/subscription IDs, Event Hubs
  namespace or hub names, storage names, endpoint or query URIs. They come from git-ignored tfvars,
  GitHub environment secrets, or deploy-time `fabric-cicd` replacement.
- Event fields exactly as spec 4.1: `event_time`, `request_id`, `session`, `api`, `operation`,
  `caller`, `answered_by`, `status`, `refused_by_gateway`, `tokens`, `latency_ms`. No client IP, no
  bodies, no deployment/model field. `caller` ∈ {`owner`, `deploy`, `webapp`, `other`}.
- The replay script's events carry `session = "replay"` and never feed a published figure.
- The ReleaseLens change is **default off**: with the switch off, the gateway's rendered policies equal
  today's files apart from the removed marker comment (a test pins this).
- Event Hubs: Standard, one hub, one partition, 1-day retention, consumer group `fabric`, local auth
  off, public network access on.
- Spark 4.1.1 / delta-spark 4.2.0 / Python 3.13 (Fabric Runtime 2.0), session TZ UTC.

## Review Focus

1. **A refused call** (the gateway's own 401/403/429) has no backend and no tokens: `answered_by` and
   `tokens` are null, `refused_by_gateway` true, and it still counts in every throttling figure.
   *(Tests in Tasks 2, 4 and 7.)*
2. **An unknown caller** (an `oid` missing from the label map) becomes `other`, never the raw ID.
   *(Test in Task 2.)*
3. **The same `request_id` twice** (Event Hubs is at-least-once): silver keeps one row; the KQL
   figures count distinct `request_id`. *(Tests in Tasks 4 and 7.)*
4. **Replay events in the same Eventhouse table:** every published query excludes
   `session == "replay"`. *(Test in Task 4.)*
5. **Mixed timestamp offsets** in `event_time`: stored and compared as UTC. *(Test in Task 7.)*

---

## File structure (new or changed)

```
contracts/gateway-event.schema.json        the event contract (Task 1)
docs/research/2026-10-23-realtime-facts.md the recon, Part 2 only (Task 1)
src/lakehouse/gateway/contract.py          EVENT_FIELDS, validate_event() (Task 1)
ReleaseLens: infra/gateway/policies/log-to-eventhub.xml, api.tf, eventhub_logger.tf,
             variables.tf, tests/infra/test_gateway_policies.py (Task 2)
infra/event-hubs/                          Terraform stack (Task 3)
fabric/kql/                                table, mapping, published queries (Task 4)
fabric/eh_gateway.Eventhouse/ … act_gateway.Reflex/  six items + parameter.yml (Task 5)
.deploy/deploy.py                          onelake-availability, ensure-gateway-shortcut (Task 6)
src/lakehouse/gateway/build.py, answers.py; fabric/nb_gateway_load.Notebook (Task 7)
tools/replay_gateway_events.py             development-only replay (Task 8)
.github/workflows/ci.yml                   validate infra/event-hubs (Task 3)
```

---

### Task 1: The event contract and the research

**Files:** Create `contracts/gateway-event.schema.json`, `src/lakehouse/gateway/__init__.py`,
`src/lakehouse/gateway/contract.py`, `docs/research/2026-10-23-realtime-facts.md`,
`tests/test_gateway_contract.py`

**Interfaces:**
- Produces: `EVENT_FIELDS: list[tuple[str, str]]` — `(name, type)` in the spec's order, types from
  {`datetime`, `string`, `int`, `bool`}: `event_time` datetime, `request_id` string, `session`
  string, `api` string, `operation` string, `caller` string, `answered_by` string (nullable),
  `status` int, `refused_by_gateway` bool, `tokens` int (nullable), `latency_ms` int.
- Produces: `CALLERS = ("owner", "deploy", "webapp", "other")`, `BACKENDS = ("primary", "secondary")`,
  `validate_event(event: dict) -> list[str]` (problems; empty = valid): exact key set, types,
  `caller` and `answered_by` membership, no GUID-shaped value anywhere, `answered_by`/`tokens` null
  iff `refused_by_gateway`.
- The JSON Schema file mirrors `EVENT_FIELDS`; a test asserts they agree.

- [ ] **Step 1:** Copy Part 2 of the recon file (path from the controller) to
  `docs/research/2026-10-23-realtime-facts.md`; drop Part 1 (it describes ReleaseLens resources).
  Grep for GUIDs and host names; expected none.
- [ ] **Step 2: Failing tests:** `test_valid_event_passes`, `test_refused_call_has_null_backend_and_tokens`
  *(Review Focus 1)*, `test_guid_value_rejected`, `test_unknown_key_rejected`,
  `test_schema_file_matches_fields`.
- [ ] **Step 3:** Run; expect FAIL. **Step 4:** Implement. **Step 5:** Run; expect PASS.
- [ ] **Step 6: Commit** `git commit -m "Add the gateway event contract and the real-time research"`

---

### Task 2: ReleaseLens — switchable Event Hubs logging (in the ReleaseLens repo)

Work on a ReleaseLens branch `p10/eventhub-logging`; this repo is untouched by this task.

**Files (ReleaseLens):** Create `infra/gateway/policies/log-to-eventhub.xml`,
`infra/gateway/eventhub_logger.tf`; Modify `infra/gateway/api.tf` (the two `file(...)` policy reads),
`infra/gateway/variables.tf`, `infra/gateway/policies/api-v1.xml` and `api-v1-rev2.xml` (one marker
comment each in `outbound` and `on-error`), `tests/infra/test_gateway_policies.py`,
`infra/gateway/tests/gateway.tftest.hcl`, `infra/gateway/README.md`

**Design (decided here):**
- Variables: `event_hub_logging` (bool, default `false`), `event_hub_namespace` (string, default
  `""`), `event_hub_name` (string, default `""`), `p10_session` (string, default `""`),
  `caller_labels_json` (string, sensitive, default `"{}"`: a JSON object `{oid: label}`).
- The policy files gain the literal marker `<!-- p10:log-to-eventhub -->` at the end of `outbound`
  (after the `x-releaselens-backend` header) and at the end of `on-error`. `api.tf` reads each as
  `replace(file(...), "<!-- p10:log-to-eventhub -->", var.event_hub_logging ? file(".../log-to-eventhub.xml") : "")`.
  With the switch off, the marker is replaced by an empty string; the marker is an XML comment, so
  the stored policy differs from today's only by that comment's removal.
- `log-to-eventhub.xml` holds one `<log-to-eventhub logger-id="p10-eventhub">` whose expression
  builds the 4.1 event as a `JObject`: `caller` from `JObject.Parse("{{caller-labels}}")[oid]` else
  `"other"`; `tokens` from `context.Variables.GetValueOrDefault<int?>("tokensConsumed")`;
  `answered_by` from the existing backend label (null in `on-error` when no backend answered);
  `refused_by_gateway` true in `on-error` when the status is 401, 403 or 429 and no backend answered;
  `session` from `{{p10-session}}`.
- `llm-token-limit` gains `tokens-consumed-variable-name="tokensConsumed"` (inert when logging is off).
- `eventhub_logger.tf` (all `count = var.event_hub_logging ? 1 : 0`): named values `caller-labels`
  (secret) and `p10-session`; an `azurerm_api_management_logger` named `p10-eventhub` with an
  `eventhub` block using the gateway's user-assigned identity client ID and endpoint
  `"${var.event_hub_namespace}.servicebus.windows.net"`. If the pinned `azurerm ~> 5.6` has no
  managed-identity `eventhub` block, use `azapi` for the logger instead, as `backends.tf` already does.

- [ ] **Step 1: Failing tests** in `test_gateway_policies.py`, each over both revisions and both switch states (render in Python with the same `replace`):
  - `test_switch_off_renders_todays_policy` (identical to the file minus the marker comment);
  - `test_log_statement_in_outbound_and_on_error_when_on`;
  - `test_event_fields_match_contract` (the `JObject` keys in `log-to-eventhub.xml` equal the 11 field names in spec 4.1);
  - `test_no_client_ip_or_body_in_event` (no `IpAddress`, no `Body`);
  - `test_unknown_caller_is_other` *(Review Focus 2)* (the expression defaults to `"other"`);
  - `test_tokens_consumed_variable_named`;
  - existing tests still pass on the rendered policies.
- [ ] **Step 2:** Run (ReleaseLens's infra pytest); expect FAIL. **Step 3:** Implement.
- [ ] **Step 4:** Run the infra tests; `terraform fmt -check`, `init -backend=false`, `validate` and
  `terraform test` for `infra/gateway` in WSL (its README's commands). Expected: all pass; the
  `tftest` gains a case with the switch off asserting zero logger resources.
- [ ] **Step 5: Commit** on the ReleaseLens branch. **Do not push**; plan 2 opens its PR with the owner.

---

### Task 3: Event Hubs Terraform

**Files:** Create `infra/event-hubs/{versions.tf,main.tf,variables.tf,outputs.tf}`; Modify
`infra/README.md`, `.github/workflows/ci.yml` (fmt/validate this stack too), `tests/test_infra.py`

**Content:** provider `azurerm ~> 4.0` (`storage_use_azuread` not needed), `subscription_id` variable
(sensitive, no default), `namespace_name` (sensitive, no default), `location` default
`australiaeast`, `sender_principal_id` (the gateway identity; sensitive), `receiver_principal_ids`
(list, default `[]`: workspace identities). Resources: resource group `rg-p10-event-hubs` (tag
`project = "p10"`); namespace Standard, capacity 1, `local_authentication_enabled = false`,
`public_network_access_enabled = true`, TLS 1.2; hub `gateway-calls`, 1 partition, 1-day retention;
consumer group `fabric`; role assignments **Azure Event Hubs Data Sender** (sender) and **Data
Receiver** (each receiver) **on the hub**, `principal_type = "ServicePrincipal"`. No outputs that name
the namespace.

- [ ] **Step 1: Failing tests** in `test_infra.py`: the stack exists and pins its provider; no GUID or
  URL in any `.tf`; Standard SKU, 1 partition, 1-day retention, consumer group `fabric`, local auth
  off; both roles scoped to the hub; sensitive variables have no defaults; CI validates it.
- [ ] **Step 2:** FAIL. **Step 3:** Write the stack. **Step 4:** `terraform fmt -check -recursive infra`,
  `init -backend=false`, `validate` (WSL) and the test file; PASS.
- [ ] **Step 5: Commit** `git commit -m "Add the session-week Event Hubs stack"`

---

### Task 4: KQL — table, mapping and the published queries

**Files:** Create `fabric/kql/gateway_calls.kql` (table + JSON ingestion mapping `gateway_calls_json`),
`fabric/kql/queries/*.kql`, `fabric/kql/PUBLISHED.md`, `tests/test_kql.py`

**Interfaces:**
- The table's columns are `EVENT_FIELDS` (Task 1) mapped to KQL types (`datetime`, `string`, `int`,
  `bool`) in the same order, plus nothing else; ingestion time comes from `ingestion_time()`.
- Every published figure has one file in `queries/`, named by its figure ID, and a row in
  `PUBLISHED.md` (ID, what it measures, file). IDs: `delay_p50_p95_max`, `refusals_per_minute`,
  `answered_by_per_minute`, `tokens_per_minute_per_caller`, `session_counts`, `failover_minutes`,
  `token_spike_minutes`.
- Each query starts from a shared function `GatewayCalls()` defined in `gateway_calls.kql` that
  filters `session != "replay"` and de-duplicates with `summarize arg_max(ingestion_time(), *) by
  request_id` *(Review Focus 3, 4)*.
- `delay_p50_p95_max` computes `ingestion_time() - event_time` in seconds: p50, p95, max and `n`.

- [ ] **Step 1: Failing tests** (text-level; no Kusto available offline): the table's columns equal
  `EVENT_FIELDS`; every query calls `GatewayCalls()`; `GatewayCalls()` contains the replay filter and the
  dedup; every file in `queries/` is in `PUBLISHED.md` and vice versa; `refusals_per_minute` counts
  `refused_by_gateway == true` without requiring `answered_by` *(Review Focus 1)*; no GUID/URL.
- [ ] **Step 2:** FAIL. **Step 3:** Write the KQL. **Step 4:** PASS.
- [ ] **Step 5: Commit** `git commit -m "Add the gateway table, its mapping and the published KQL"`

---

### Task 5: The six Fabric real-time items

**Files:** Create `fabric/eh_gateway.Eventhouse/`, `fabric/kql_gateway.KQLDatabase/`,
`fabric/kq_gateway.KQLQueryset/`, `fabric/rtd_gateway.KQLDashboard/`, `fabric/act_gateway.Reflex/`,
`fabric/es_gateway.Eventstream/` (each with `.platform` + its definition files); Modify
`fabric/parameter.yml`, `fabric/VERIFY.md`, `tools/check_hygiene.py`, `tests/test_fabric_items.py`

**Content (formats from Microsoft's item-definition docs and `fabric-cicd` samples; anything
unconfirmed goes in `VERIFY.md`, never guessed silently):**
- KQL database: schema script = `fabric/kql/gateway_calls.kql` content.
- Queryset and dashboard: queries copied from `fabric/kql/queries/` (a test asserts the text matches);
  the data source URI is the token `$items.Eventhouse.eh_gateway.$queryserviceuri` via `parameter.yml`.
- Dashboard tiles: one per published figure except `delay_p50_p95_max` (a Stat tile), with the
  failover and token-spike tiles as Stat/Column types with a fixed time range (Activator needs that).
- Reflex: two rules on those two tiles; threshold for the token spike is a parameter defaulting to
  `5000` tokens per caller per minute (below the gateway's 10,000 limit; plan 2 confirms it).
- Eventstream: source Event Hubs with namespace, hub, consumer group `fabric` and workspace-identity
  auth; namespace and hub are `$ENV:P10_EVENTHUB_NAMESPACE` / `$ENV:P10_EVENTHUB_NAME` replacements
  (feature flag `enable_environment_variable_replacement`); destination the Eventhouse table with
  mapping `gateway_calls_json`.
- `parameter.yml`: the replacements above, `PROD` only; dev gets the same definitions (stream stopped
  is a plan-2 runbook step).
- Hygiene: reject any committed `servicebus.windows.net` or `kusto.fabric.microsoft.com` host.

- [ ] **Step 1: Failing tests:** every `.platform` parses with a unique logicalId; the database schema
  equals `gateway_calls.kql`; queryset and dashboard queries equal the files in `fabric/kql/queries/`;
  the two alert tiles are Stat/Column with a fixed time range; the Reflex has exactly two rules on
  those tiles; the Eventstream has no literal namespace/hub; `parameter.yml` names exist; hygiene
  flags the two hosts.
- [ ] **Step 2:** FAIL. **Step 3:** Write the items. **Step 4:** PASS (plus project 9's item tests).
- [ ] **Step 5: Commit** `git commit -m "Add the Eventhouse, KQL, dashboard, Activator and Eventstream items"`

---

### Task 6: Deploy script — OneLake availability and the gateway shortcut

**Files:** Modify `.deploy/deploy.py`, `.github/workflows/deploy.yml`, `tests/test_deploy.py`,
`tests/test_workflows.py`

**Interfaces:**
- `onelake-availability` — env `FABRIC_WORKSPACE_ID`: looks up `kql_gateway` by display name
  (`GET /v1/workspaces/{ws}/kqlDatabases`, paged like `_lakehouse_id`), then runs the management
  command `.alter-merge table gateway_calls policy mirroring dataformat=parquet with (IsEnabled=true, TargetLatencyInMinutes=5)`
  against the database's query URI with a Kusto-scoped token. Idempotent. Check the exact command
  text against Microsoft Learn's OneLake availability page and cite it in a comment; if it differs,
  follow Learn and note it in `fabric/VERIFY.md`.
- `ensure-gateway-shortcut` — env `FABRIC_WORKSPACE_ID`: creates (CreateOrOverwrite) a OneLake shortcut
  in `lh_portfolio` at `Tables`, name `gateway_calls`, target `oneLake {workspaceId, itemId: kql_gateway,
  path: "Tables/gateway_calls"}`.
- Same error rules as today: status before body; only the step and exception type on failure.
- `deploy.yml` prod job: after `publish`, run `onelake-availability` then `ensure-gateway-shortcut`.

- [ ] **Step 1: Failing tests:** command text and target URI built from the looked-up database; the
  shortcut payload; both idempotent; not-found exits 1 without IDs; workflow order.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS (`test_deploy.py`, `test_workflows.py`).
- [ ] **Step 5: Commit** `git commit -m "Deploy OneLake availability and the gateway shortcut"`

---

### Task 7: Lakehouse — silver, gold, the check and the answer

**Files:** Create `src/lakehouse/gateway/build.py`, `src/lakehouse/gateway/answers.py`,
`fabric/nb_gateway_load.Notebook/`; Modify `src/lakehouse/fabric_entry.py`,
`src/lakehouse/seeds/rg_rules.csv`, `src/lakehouse/gold/build.py` (`PROJECTS` gains
`("p10", "Fabric Real-Time Intelligence")`), tests

**Interfaces:**
- `build_gateway_calls(spark, source_path: str) -> DataFrame`: reads the shortcut table (Delta),
  validates columns against `EVENT_FIELDS`, normalises `event_time` to UTC *(Review Focus 5)*, keeps
  one row per `request_id` (latest ingestion) *(Review Focus 3)*, drops `session == "replay"`.
- `merge_gateway_calls(spark, df, table_path)`: Delta MERGE on `request_id`; a re-run is a no-op.
- `build_fact_gateway_call(spark, paths) -> int` writes gold `fact_gateway_call` (grain: one call;
  joins `dim_date` on `event_time`'s date and `dim_project` = `p4`); `GRAINS` gains its line.
- `check_gateway_counts(spark, paths, session: str, eventhouse_count: int) -> CheckResult`: gold
  count for the session equals the Eventhouse's distinct count (passed in by the notebook).
- `cost_per_gateway_call(spark, paths, session: str) -> DataFrame`: the session's calls, the day's
  attributed cost of `rg-releaselens-gateway` (project 9's `fact_daily_cost`) in its billing currency,
  per call; null when the cost day has no rows yet.
- `fabric_entry.load_gateway(session: str, eventhouse_count: str, context: dict) -> dict`.
- Seeds: `rg-p10-*` → `p10` rule with evidence; `test_gold`'s project count becomes 12.

- [ ] **Step 1: Failing tests:** dedup on `request_id`; replay dropped; refused calls kept with null
  backend/tokens *(Review Focus 1)*; `+10:00` and `Z` times equal *(Review Focus 5)*; MERGE re-run
  inserts 0, updates 0; count check passes and fails; cost per call with and without cost rows;
  `p10` attribution; notebook item test (parameter cell, `%pip` first, calls `load_gateway`).
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS (focused files).
- [ ] **Step 5: Commit** `git commit -m "Add gateway silver, gold, count check and cost per call"`

---

### Task 8: The development-only replay script

**Files:** Create `tools/replay_gateway_events.py`, `tests/test_replay.py`; Modify `pyproject.toml`
(`azure-eventhub` in a `replay` extra)

**Interfaces:** `make_events(n: int, start: datetime, seed: int) -> list[dict]` — every event valid
under `validate_event`, `session = "replay"`, a mix of callers, both backends and some refusals;
`send(events, namespace: str, hub: str, sender=None) -> int` with an injectable sender (default:
`EventHubProducerClient` with `AzureCliCredential`); CLI `--count`, env `P10_EVENTHUB_NAMESPACE`,
`P10_EVENTHUB_NAME`. The module docstring and `--help` say it is for development only.

- [ ] **Step 1: Failing tests:** all events valid; all `session == "replay"`; deterministic for a seed;
  `send` uses the injected sender and returns the count; no identifiers printed.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS.
- [ ] **Step 5: Commit** `git commit -m "Add the development-only gateway event replay"`

---

## After plan 1

- Full suite and hygiene; PR in this repo (owner merges); the ReleaseLens branch stays unpushed until
  plan 2.
- Tracker line; then plan 2, the live runbook: Event Hubs apply, ReleaseLens PR and gateway session,
  the replay test in dev, the measured session in prod, evidence, teardown, the cost answer next day.
